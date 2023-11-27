# Silva Datasets
declare -A datasets=(
    [silva-nr99]=silva_nr99
    [silva-nr99-filtered]=silva_nr99_filtered
    [silva-nr99-filtered-515f-806r]=silva_nr99_filtered_515f_806r
)

# DNABERT
declare -A dnabert_pretrain_artifacts=(
    [silva-nr99]=sirdavidludwig/model-registry/dnabert-pretrain-64d-150bp:silva-nr99
    [silva-nr99-filtered]=sirdavidludwig/model-registry/dnabert-pretrain-64d-150bp:silva-nr99-filtered
    [silva-nr99-filtered-515f-806r]=sirdavidludwig/model-registry/dnabert-pretrain-64d-150bp:silva-nr99-filtered-515f-806r
)
