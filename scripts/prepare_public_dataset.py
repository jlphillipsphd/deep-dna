#!/usr/bin/env python3
"""
Prepare a public taxonomic dataset such as Greengenes or Silva. This script will
download the dataset automatically and create the appropriate files for use in
Qiime as well as our own deep learning models.
"""
from dnadb.datasets import get_datasets
from dnadb.datasets.dataset import InterfacesWithFasta, InterfacesWithTaxonomy, VersionedDataset
from dnadb import fasta, taxonomy
from dnadb.utils import compress
from itertools import chain
import numpy as np
from pathlib import Path
import sys
import tf_utilities.scripting as tfs
from tqdm.auto import tqdm, trange
from typing import cast, TextIO

import bootstrap

# Type Definition
class FastaDataset(InterfacesWithFasta, InterfacesWithTaxonomy, VersionedDataset):
    ...

def define_arguments(cli: tfs.CliArgumentFactory):
    cli.use_rng()
    cli.argument("output_path", help="The path where the files will be written")
    cli.argument("--test-split", type=float, default=0.0, help="The factor of the number of samples to use for testing")
    cli.argument("--num-splits", type=int, default=1, help=f"The number of data splits to create")
    cli.argument("--min-length", type=int, default=0, help="The minimum length of a sequence to include")
    cli.argument("--force-download", default=False, action="store_true", help="Force re-downloading of data")
    output_types = cli.parser.add_argument_group("Output Formats")
    output_types.add_argument("--output-db", default=False, action="store_true", help="Output FASTA DBs")
    output_types.add_argument("--output-fasta", default=False, action="store_true", help="Output FASTA + taxonomy TSV files")
    output_types.add_argument("--compress", default=False, action="store_true", help="Compress the output FASTA/TSV files")
    dataset_names = cli.parser.add_argument_group("Datasets", "Available datasets to use")
    for dataset in get_datasets():
        dataset_names.add_argument(
            f"--use-{dataset.NAME.lower()}",
            nargs='?',
            default=None,
            const=dataset.DEFAULT_VERSION,
            metavar=f"{dataset.NAME.upper()}_VERSION",
            help=f"Use the {dataset.NAME} dataset")


def load_train_labels(config, datasets: list[FastaDataset], rng: np.random.Generator):
    taxonomies = chain(*[dataset.taxonomies() for dataset in datasets])
    labels = list(tqdm(taxonomy.unique_labels(taxonomies), desc="Loading unique labels", leave=False))
    rng.shuffle(labels)
    return set(labels[int(config.test_split*len(labels)):])


def dataset_file_names(datasets: list[FastaDataset]) -> tuple[str, str]:
    name = "-".join([f"{d.name}_{d.version}" for d in datasets])
    return name + ".fasta", name + ".tax.tsv"


def output_fasta(
    config,
    datasets: list[FastaDataset],
    train_labels: set[str],
    hierarchy: taxonomy.TaxonomyHierarchy,
    train_path: Path,
    test_path: Path|None
) -> list[Path]:
    sequences_file_name, taxonomy_file_name = dataset_file_names(datasets)
    train_fasta = open(train_path / sequences_file_name, 'w')
    train_tax = open(train_path / taxonomy_file_name, 'w')
    files: list[TextIO] = [train_fasta, train_tax]
    if test_path is not None:
        test_fasta = open(test_path / sequences_file_name, 'w')
        test_tax = open(test_path / taxonomy_file_name, 'w')
        files += [test_fasta, test_tax]
    sequences = chain(*[dataset.sequences() for dataset in datasets])
    taxonomies = chain(*[dataset.taxonomies() for dataset in datasets])
    for sequence, tax in tqdm(fasta.entries_with_taxonomy(sequences, taxonomies), leave=False, desc="Writing Fasta/Taxonomy entries"):
        if len(sequence.sequence) < config.min_length:
            continue
        fasta_out, tax_out = (train_fasta, train_tax)
        if tax.label not in train_labels:
            tax = hierarchy.reduce_entry(tax)
            fasta_out, tax_out = (test_fasta, test_tax) # type: ignore
        fasta_out.write(str(sequence) + '\n')
        tax_out.write(str(tax) + '\n')
    for file in files:
        file.close()
    return [Path(file.name) for file in files]


def output_db(
    config,
    datasets: list[FastaDataset],
    train_labels: set[str],
    hierarchy: taxonomy.TaxonomyHierarchy,
    train_path: Path,
    test_path: Path|None
):
    sequences_file_name, taxonomy_file_name = dataset_file_names(datasets)
    train_fasta = fasta.FastaDbFactory(train_path / sequences_file_name)
    train_tax = taxonomy.TaxonomyDbFactory(train_path / taxonomy_file_name)
    if test_path is not None:
        test_fasta = fasta.FastaDbFactory(test_path / sequences_file_name)
        test_tax = taxonomy.TaxonomyDbFactory(test_path / taxonomy_file_name)
    sequences = chain(*[dataset.sequences() for dataset in datasets])
    taxonomies = chain(*[dataset.taxonomies() for dataset in datasets])
    for sequence, tax in tqdm(fasta.entries_with_taxonomy(sequences, taxonomies), leave=False, desc="Writing DB entries"):
        if len(sequence.sequence) < config.min_length:
            continue
        fasta_out, tax_out = (train_fasta, train_tax)
        if tax.label not in train_labels:
            tax = hierarchy.reduce_entry(tax)
            fasta_out, tax_out = (test_fasta, test_tax) # type: ignore
        fasta_out.write_entry(sequence)
        tax_out.write_entry(tax)


def main():
    config = tfs.init(define_arguments, use_wandb=False)

    output_path = Path(config.output_path)

    datasets: list[FastaDataset] = []
    for dataset in get_datasets():
        if (version := getattr(config, f"use_{dataset.NAME.lower()}")) is None:
            continue
        datasets.append(cast(FastaDataset, dataset(version=version)))

    if len(datasets) == 0:
        print("No datasets selected. Provide at least one dataset (i.e. Silva, Greengenes, etc.)")
        return 1

    if not output_path.parent.exists():
        print(f"The output directory: `{output_path.parent}` does not exist.")
        return 1

    if config.num_splits > 1 and config.test_split == 0.0:
        print("Num splits can only be used when a test split > 0.0 is supplied.")
        return 1

    rng = tfs.rng()
    fasta_files: list[Path] = []

    for i in trange(config.num_splits, desc="Dataset splits"):

        # Fetch the unique labels used for training
        train_labels = load_train_labels(config, datasets, rng)

        # Create the taxonomy hierarchy for training labels
        hierarchy = taxonomy.TaxonomyHierarchy.from_labels(train_labels, depth=6)

        # Create the directories
        train_path = output_path
        test_path = None
        if config.test_split > 0.0:
            train_path = output_path / str(i)
            test_path = train_path / "test"
            train_path = train_path / "train"
            test_path.mkdir(parents=True, exist_ok=True)
        train_path.mkdir(parents=True, exist_ok=True)

        if config.output_fasta:
            fasta_files += output_fasta(
                config,
                datasets,
                train_labels,
                hierarchy,
                train_path,
                test_path)

        if config.output_db:
            output_db(
                config,
                datasets,
                train_labels,
                hierarchy,
                train_path,
                test_path)

        if config.compress and len(fasta_files):
            for file in tqdm(fasta_files, description="Compressing"):
                compress(file)

if __name__ == "__main__":
    sys.exit(main())