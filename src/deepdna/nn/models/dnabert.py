from dnadb.taxonomy import TaxonomyHierarchy
import json
import tensorflow as tf
import tf_utilities as tfu
from typing import cast

from .custom_model import ModelWrapper, CustomModel
from .. import layers
from ..metrics import TaxonCategoricalAccuracy
from ..losses import TaxonCategoricalCrossentropy
from ..registry import CustomObject

@CustomObject
class DnaBertModel(ModelWrapper, CustomModel[tf.Tensor, tf.Tensor]):
    """
    The base DNABERT model definition.
    """
    def __init__(
        self,
        sequence_length: int,
        kmer: int,
        embed_dim: int,
        stack: int,
        num_heads: int,
        pre_layernorm: bool = True,
        variable_length: bool = False,
        num_bases: int = 4,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.sequence_length = sequence_length
        self.kmer = kmer
        self.embed_dim = embed_dim
        self.stack = stack
        self.num_heads = num_heads
        self.pre_layernorm = pre_layernorm
        self.variable_length = variable_length
        self.num_bases = num_bases
        self.model = self.build_model()

    def build_model(self):
        additional_tokens = 1 # mask token
        additional_tokens += int(self.variable_length) # padding token
        y = x = tf.keras.layers.Input((self.sequence_length - self.kmer + 1), dtype=tf.int32)
        y = layers.EmbeddingWithClassToken(
            self.num_bases**self.kmer + additional_tokens,
            self.embed_dim,
            mask_zero=True)(y)
        for _ in range(self.stack):
            y = layers.RelativeTransformerBlock(
                embed_dim=self.embed_dim,
                num_heads=self.num_heads,
                ff_dim=self.embed_dim,
                prenorm=self.pre_layernorm)(y)
        return tf.keras.Model(x, y)

    def get_config(self):
        return super().get_config() | {
            "sequence_length": self.sequence_length,
            "kmer": self.kmer,
            "embed_dim": self.embed_dim,
            "stack": self.stack,
            "num_heads": self.num_heads,
            "pre_layernorm": self.pre_layernorm,
            "variable_length": self.variable_length,
            "num_bases": self.num_bases
        }


@CustomObject
class DnaBertPretrainModel(ModelWrapper, CustomModel[tf.Tensor, tf.Tensor]):
    """
    The DNABERT pretraining model architecture
    """
    def __init__(
        self,
        base: DnaBertModel,
        mask_ratio: float = 0.15,
        min_len: int|None = None,
        max_len: int|None = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.base = base
        self.max_len = self.base.sequence_length if max_len is None else max_len
        self.min_len = self.max_len if min_len is None else min_len
        assert self.min_len <= self.max_len
        if self.base.variable_length:
            self.masking = layers.TrimAndContiguousMask(
                self.min_len - self.base.kmer + 1,
                self.max_len - self.base.kmer + 1,
                self.mask_ratio)
        else:
            self.masking = layers.ContiguousMask(mask_ratio)
        self.model = self.build_model()

    def build_model(self):
        additional_tokens = 1 + int(self.base.variable_length)
        y = x = tf.keras.layers.Input(
            (self.base.sequence_length - self.base.kmer + 1,),
            dtype=tf.int32)
        y = tf.keras.layers.Lambda(lambda x: x + additional_tokens)(y) # Make room for mask
        y = self.masking(cast(tf.Tensor, y))
        y = self.base(y)
        _, y = layers.SplitClassToken()(y)
        y = layers.InvertMask()(y)
        y = tf.keras.layers.Dense(self.base.num_bases**self.base.kmer)(y)
        return tf.keras.Model(x, y)

    def compile(self, **kwargs):
        if "loss" not in kwargs:
            kwargs["loss"] = tf.keras.losses.SparseCategoricalCrossentropy(from_logits=True)
        super().compile(**kwargs)

    def compute_output_shape(self, input_shape):
        return self.model.compute_output_shape(input_shape)

    def get_config(self):
        return super().get_config() | {
            "base": self.base,
            "min_len": self.min_len,
            "max_len": self.max_len,
            "mask_ratio": self.masking.mask_ratio.numpy() # type: ignore
        }


@CustomObject
class DnaBertEncoderModel(ModelWrapper, CustomModel[tf.Tensor, tf.Tensor]):
    """
    The DNABERT encoder/embedding model architecture
    """
    def __init__(self, base: DnaBertModel, chunk_size: int, **kwargs):
        super().__init__(**kwargs)
        self.base = base
        self.chunk_size = chunk_size
        self.model = self.build_model()

    def build_model(self):
        y = x = tf.keras.layers.Input(self.base.input_shape[1:], dtype=tf.int32)
        y = tf.keras.layers.Lambda(lambda x: x + 1)(y)
        y = self.base(y)
        token, _ = layers.SplitClassToken()(y)
        return tf.keras.Model(x, token)

    def encode(self, batch: tf.Tensor, chunk_size: int|None = None):
        chunk_size = chunk_size if chunk_size is not None else self.chunk_size
        original_shape = tf.shape(batch)
        batch = tf.reshape(batch, (-1, original_shape[-1]))
        result = tfu.subbatching.subbatch_predict(self, batch, chunk_size)
        return tf.reshape(result, tf.concat((original_shape[:-1], (-1,)), axis=0))

    def get_config(self):
        return super().get_config() | {
            "base": self.base,
            "chunk_size": self.chunk_size
        }


@CustomObject
class DnaBertTaxonomyModel(ModelWrapper, CustomModel[tf.Tensor, layers.TaxonomyOutputDict]):
    def __init__(
        self,
        base: DnaBertModel,
        hierarchy: TaxonomyHierarchy,
        use_top_down_hierarchy: bool = False,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.base = base
        self.hierarchy = hierarchy
        self.use_top_down_hierarchy = use_top_down_hierarchy
        if self.use_top_down_hierarchy:
            self.hierarchy_block = layers.TaxonomyHierarchyBlock.from_hierarchy(
                self.hierarchy,
                output_logits=False)
        else:
            self.hierarchy_block = layers.TaxonomyBlock.from_hierarchy(self.hierarchy)
        self.model = self.build_model()

    def build_model(self):
        y = x = tf.keras.layers.Input(self.base.input_shape[1:], dtype=self.base.input.dtype)
        y = self.base(y)
        y, _ = layers.SplitClassToken()(y)
        y = self.hierarchy_block(y)
        return tf.keras.Model(x, y)

    def compile(self, **kwargs):
        defaults = {
            "loss": TaxonCategoricalCrossentropy(from_logits=False),
            "metrics": []
        }
        kwargs = defaults | kwargs
        kwargs["metrics"].append(TaxonCategoricalAccuracy(name="accuracy"))
        return super().compile(**kwargs)

    def get_config(self):
        return super().get_config() | {
            "base": self.base,
            "hierarchy": json.dumps(self.hierarchy.to_json()),
            "use_top_down_hierarchy": self.use_top_down_hierarchy
        }

    @classmethod
    def from_config(cls, config):
        config["hierarchy"] = TaxonomyHierarchy.from_json(json.loads(config["hierarchy"]))
        return super().from_config(config)

    @property
    def output_names(self):
        return self.hierarchy_block.output_names