#!/usr/bin/env bash
set -o errexit
set -o pipefail

# Usage: kubeconform.sh <kubernetes-dir> [cluster ...]
#
# <kubernetes-dir> is the repo's kubernetes/ directory. Each cluster lives in its
# own subdirectory (kubernetes/main, kubernetes/edge), so validation is scoped per
# cluster: the standalone manifests and kustomizations under <cluster>/flux, and
# the kustomizations under <cluster>/apps. With no cluster arguments, every cluster
# directory found under <kubernetes-dir> is validated.
#
# kubernetes/shared/components is deliberately not walked directly: those are
# Kustomize Components, which are only buildable when pulled into a parent via
# `components:`. They are validated transitively through the app kustomizations
# that consume them.
#
# Flux resolves `postBuild.substitute` variables at reconcile time, so a raw
# `kustomize build` leaves `${APP}`, `${VOLSYNC_CAPACITY}` and friends in place and
# every resource carrying one fails schema validation. Each app's substitutions are
# therefore read from its ks.yaml and applied before kubeconform sees the manifest,
# so what gets validated is what Flux will actually apply.

KUBERNETES_DIR=$1
shift || true
CLUSTERS=("$@")
EXPLICIT_CLUSTERS=$#

[[ -z "${KUBERNETES_DIR}" ]] && echo "Kubernetes location not specified" && exit 1
[[ ! -d "${KUBERNETES_DIR}" ]] && echo "Kubernetes location ${KUBERNETES_DIR} does not exist" && exit 1

KUBERNETES_DIR=$(cd "${KUBERNETES_DIR}" && pwd)
# ks.yaml `spec.path` values are relative to the repo root, one level above kubernetes/
REPO_ROOT=$(dirname "${KUBERNETES_DIR}")

# Auto-discover clusters when none were requested: a cluster directory is one
# that holds a flux/ or an apps/ directory.
if [[ ${EXPLICIT_CLUSTERS} -eq 0 ]]; then
    while IFS= read -r dir; do
        CLUSTERS+=("$(basename "${dir}")")
    done < <(find "${KUBERNETES_DIR}" -mindepth 1 -maxdepth 1 -type d | sort)
fi

kustomize_args=("--load-restrictor=LoadRestrictionsNone")
kustomize_config="kustomization.yaml"
kubeconform_args=(
    "-strict"
    "-ignore-missing-schemas"
    "-skip"
    "Secret"
    "-schema-location"
    "default"
    "-schema-location"
    "https://kubernetes-schemas.pages.dev/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
    "-verbose"
)

# Extracts "<spec.path>\t<KEY>=<VALUE>" for every postBuild substitution in a ks.yaml.
# explode(.) resolves the `&app`/`*app` anchors these files lean on.
yq_substitutions='
    explode(.)
    | select(.kind == "Kustomization" and .spec.path != null)
    | .spec.path as $path
    | (.spec.postBuild.substitute // {})
    | to_entries
    | .[]
    | $path + "\t" + .key + "=" + (.value | tostring)
'

# Applies Flux postBuild substitution semantics to stdin, driven by FLUX_SUBS
# (a \x01-separated list of KEY=VALUE). `$${...}` is Flux's escape for a literal
# `${...}`, so it is masked before substitution and restored after. A `${VAR}` with
# no known value and no default is left untouched so it still fails loudly.
perl_substitute='
    BEGIN {
        %subs = map { /^([^=]+)=(.*)$/s ? ($1 => $2) : () }
                split /\x01/, ($ENV{FLUX_SUBS} // "");
    }
    s/\$\$/\x02/g;
    s/\$\{(\w+)(?::[-=]([^}]*))?\}/
        exists $subs{$1} ? $subs{$1} : (defined $2 ? $2 : "\${$1}")
    /ge;
    s/\x02/\$/g;
'

declare -A SUBSTITUTIONS

function index_substitutions() {
    local dir=$1 path kv
    [[ ! -d "${dir}" ]] && return 0
    while IFS=$'\t' read -r path kv; do
        [[ -z "${path}" || -z "${kv}" ]] && continue
        path="./${path#./}"
        if [[ -n "${SUBSTITUTIONS[${path}]:-}" ]]; then
            SUBSTITUTIONS[${path}]="${SUBSTITUTIONS[${path}]}"$'\x01'"${kv}"
        else
            SUBSTITUTIONS[${path}]="${kv}"
        fi
    done < <(
        while IFS= read -r -d $'\0' file; do
            yq eval-all "${yq_substitutions}" "${file}"
        done < <(find "${dir}" -type f -name 'ks.yaml' -print0)
    )
}

function validate_standalone_manifests() {
    local dir=$1
    [[ ! -d "${dir}" ]] && return 0
    echo "=== Validating standalone manifests in ${dir} ==="
    while IFS= read -r -d $'\0' file; do
        kubeconform "${kubeconform_args[@]}" "${file}"
    done < <(find "${dir}" -maxdepth 1 -type f -name '*.yaml' -print0)
}

function validate_kustomizations() {
    local dir=$1 target rel
    [[ ! -d "${dir}" ]] && return 0
    while IFS= read -r -d $'\0' file; do
        target="${file/%$kustomize_config}"
        rel="./${target#"${REPO_ROOT}"/}"
        rel="${rel%/}"
        echo "=== Validating kustomizations in ${target} ==="
        kustomize build "${target}" "${kustomize_args[@]}" |
            FLUX_SUBS="${SUBSTITUTIONS[${rel}]:-}" perl -pe "${perl_substitute}" |
            kubeconform "${kubeconform_args[@]}"
    done < <(find "${dir}" -type f -name "${kustomize_config}" -print0)
}

validated=0
for cluster in "${CLUSTERS[@]}"; do
    cluster_dir="${KUBERNETES_DIR}/${cluster}"
    if [[ ! -d "${cluster_dir}/flux" && ! -d "${cluster_dir}/apps" ]]; then
        # Not a cluster directory (e.g. kubernetes/shared); skip silently when
        # auto-discovered, but complain when it was asked for explicitly.
        if [[ ${EXPLICIT_CLUSTERS} -gt 0 ]]; then
            echo "=== ${cluster_dir} has no flux/ or apps/ directory ===" && exit 1
        fi
        continue
    fi

    echo "=== Validating cluster ${cluster} ==="
    index_substitutions "${cluster_dir}"
    validate_standalone_manifests "${cluster_dir}/flux"
    validate_kustomizations "${cluster_dir}/flux"
    validate_kustomizations "${cluster_dir}/apps"
    validated=$((validated + 1))
done

[[ ${validated} -eq 0 ]] && echo "No clusters found under ${KUBERNETES_DIR}" && exit 1

echo "=== Validated ${validated} cluster(s) ==="
