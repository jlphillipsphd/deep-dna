from pathlib import Path
import tensorflow as tf
from typing import Any

def load_model(
    path: str|Path,
    custom_objects: dict[str, Any]|None = None,
    compile: bool = True,
    options: tf.saved_model.LoadOptions|None = None
):
    """
    Load a custom model, providing the necessary custom object layers
    """
    from . import layers, models, registry
    objects = registry.custom_objects()
    if custom_objects is not None:
        objects.update(custom_objects)
    return tf.keras.models.load_model(path, custom_objects, compile, options)