#!/usr/bin/env bash
# Interactive key registration for the media-studio asset sources.
#
#   scripts/keys-wizard.sh           prompt for each missing key
#   scripts/keys-wizard.sh --check   report which keys are registered, masked
#
# Values are read without echo, never printed, and stored in a mode-0600 file
# outside every git repository. Re-running the wizard is safe: it rewrites one
# variable per provider and leaves every other line untouched.
set -euo pipefail

CONFIG_DIR="${OPENCODE_CONFIG_DIR:-${HOME}/.config/opencode}"
SECRETS_FILE="${CONFIG_DIR}/secrets.env"

# NAME|label|used for|signup page|status
#
# status is one of:
#   required              the toolkit needs it; a missing value fails --check
#   optional:<reason>     useful, but the toolkit works without it
#   deferred:<reason>     cannot be obtained now, for a reason outside this project
#
# Only `required` counts towards --check, so a reported problem is always something
# the user can act on.
PROVIDERS=(
  "PIXABAY_API_KEY|Pixabay|video, photos, and audio clips|https://pixabay.com/api/docs/|required"
  "FREESOUND_API_KEY|Freesound|sound effects and field recordings|https://freesound.org/apiv2/apply/|required"
  "JAMENDO_CLIENT_ID|Jamendo|Creative Commons music|https://devportal.jamendo.com/|required"
  "SKETCHFAB_API_TOKEN|Sketchfab|3D models (download requires a token)|https://sketchfab.com/settings/password|required"
  "UNSPLASH_ACCESS_KEY|Unsplash|photography and textures|https://unsplash.com/oauth/applications|optional:the stocky skill uses it, the ms CLI does not"
  "BLENDERKIT_API_KEY|BlenderKit|Blender material and model library|https://www.blenderkit.com/account/|optional:only needed for BlenderKit downloads"
  "GEMINI_API_KEY|Google AI Studio|Imagen / Veo generative images and video|https://aistudio.google.com/app/apikey|optional:free-quota generative layer, skipped without a key"
  "HF_TOKEN|Hugging Face|inference API image generation|https://huggingface.co/settings/tokens|optional:free-quota generative layer, skipped without a key"
  "PEXELS_API_KEY|Pexels|video and photo clips|https://www.pexels.com/api/new/|deferred:account suspended by the provider, key creation unavailable"
)

# Provider-specific minimum length. Most credentials are long opaque tokens, but Jamendo issues an
# 8-character client id which is a valid credential on its own, and a blanket minimum refuses it.
declare -A MINIMUM_LENGTH=(
  [JAMENDO_CLIENT_ID]=8
)
DEFAULT_MINIMUM_LENGTH=16

usage() {
  sed -n '2,7p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

mask() {
  local value=$1
  if [[ ${#value} -le 8 ]]; then
    printf 'set (%d chars)' "${#value}"
  else
    printf '%s...%s (%d chars)' "${value:0:4}" "${value: -4}" "${#value}"
  fi
}

split_status() {
  # Sets STATUS_KIND and STATUS_REASON from the provider's status field.
  local status=$1
  if [[ ${status} == *:* ]]; then
    STATUS_KIND=${status%%:*}
    STATUS_REASON=${status#*:}
  else
    STATUS_KIND=${status:-required}
    STATUS_REASON=""
  fi
}

current_value() {
  local name=$1 raw
  if [[ ! -f "${SECRETS_FILE}" ]]; then
    return 0
  fi
  raw="$(sed -n "s/^[[:space:]]*${name}=//p" "${SECRETS_FILE}" | tail -n 1)"
  raw="${raw%\"}"
  raw="${raw#\"}"
  raw="${raw%\'}"
  raw="${raw#\'}"
  printf '%s' "${raw}"
}

write_secret() {
  local name=$1 value=$2 tmp
  mkdir -p "${CONFIG_DIR}"
  tmp="$(mktemp "${CONFIG_DIR}/.secrets.env.XXXXXX")"
  if [[ -f "${SECRETS_FILE}" ]]; then
    grep -v -E "^[[:space:]]*${name}=" "${SECRETS_FILE}" >"${tmp}" || true
  else
    printf '# Secrets for the OpenCode agent. Never commit this file.\n' >"${tmp}"
  fi
  printf '%s=%s\n' "${name}" "${value}" >>"${tmp}"
  chmod 600 "${tmp}"
  mv "${tmp}" "${SECRETS_FILE}"
}

validate_key() {
  # Two statements on purpose: within one `local`, an earlier assignment is not yet in effect when a
  # later one references it (shellcheck SC2318), so the name has to exist first.
  local name=$1
  local value=$2 minimum=${MINIMUM_LENGTH[${name}]:-${DEFAULT_MINIMUM_LENGTH}}
  if [[ ${#value} -lt ${minimum} ]]; then
    printf '  too short: %s needs at least %d characters, got %d\n' "${name}" "${minimum}" "${#value}"
    return 1
  fi
  if [[ "${value}" =~ [[:space:]] ]]; then
    printf '  contains whitespace: paste the key on its own, without quotes\n'
    return 1
  fi
  return 0
}

prompt_key() {
  local name=$1 label=$2 url=$3 value
  printf '\n%s\n  signup: %s\n' "${label}" "${url}"
  for _ in 1 2 3; do
    printf '  paste %s (hidden, empty skips): ' "${name}"
    IFS= read -r -s value || true
    printf '\n'
    if [[ -z "${value}" ]]; then
      printf '  left unchanged\n'
      return 0
    fi
    if validate_key "${name}" "${value}"; then
      write_secret "${name}" "${value}"
      printf '  saved %s\n' "$(mask "${value}")"
      return 0
    fi
  done
  printf '  skipped %s after 3 attempts\n' "${name}"
}

run_interactive() {
  local entry name label purpose url existing answer
  if [[ ! -t 0 ]]; then
    printf 'error: needs an interactive terminal; run it from a shell\n' >&2
    exit 2
  fi

  printf 'media-studio key wizard\n'
  printf 'target: %s (mode 0600, outside git)\n' "${SECRETS_FILE}"
  printf 'Keys are typed into the terminal only. They are never echoed and never\n'
  printf 'appear in the agent transcript. Press Ctrl+C at any time.\n'

  for entry in "${PROVIDERS[@]}"; do
    IFS='|' read -r name label purpose url status <<<"${entry}"
    split_status "${status}"
    existing="$(current_value "${name}")"
    if [[ -n "${existing}" ]]; then
      printf '\n%s\n  already registered: %s\n  replace? [y/N]: ' "${label}" "$(mask "${existing}")"
      IFS= read -r answer || true
      case "${answer}" in
        y | Y | yes | YES) prompt_key "${name}" "${label}" "${url}" ;;
        *) printf '  kept\n' ;;
      esac
    elif [[ ${STATUS_KIND} == "deferred" ]]; then
      printf '\n%s\n  deferred: %s\n  skipping; re-run this wizard once the provider is available\n' \
        "${label}" "${STATUS_REASON}"
    else
      printf '\n%s (%s)\n' "${label}" "${purpose}"
      [[ ${STATUS_KIND} == "optional" ]] && printf '  optional: %s\n' "${STATUS_REASON}"
      prompt_key "${name}" "${label}" "${url}"
    fi
  done

  if [[ -f "${SECRETS_FILE}" ]]; then
    chmod 600 "${SECRETS_FILE}"
    printf '\nwrote %s\n' "${SECRETS_FILE}"
    run_check || true
  fi
}

run_check() {
  local entry name label purpose url status value missing=0 optional=0 deferred=0
  printf '\nregistered keys\n'
  for entry in "${PROVIDERS[@]}"; do
    IFS='|' read -r name label purpose url status <<<"${entry}"
    split_status "${status}"
    value="$(current_value "${name}")"
    if [[ -n "${value}" ]]; then
      printf '  %-22s %s\n' "${name}" "$(mask "${value}")"
    elif [[ ${STATUS_KIND} == "deferred" ]]; then
      printf '  %-22s deferred (%s)\n' "${name}" "${STATUS_REASON}"
      deferred=$((deferred + 1))
    elif [[ ${STATUS_KIND} == "optional" ]]; then
      printf '  %-22s optional, not set (%s)\n' "${name}" "${STATUS_REASON}"
      optional=$((optional + 1))
    else
      printf '  %-22s missing\n' "${name}"
      missing=$((missing + 1))
    fi
  done
  if [[ -f "${SECRETS_FILE}" ]]; then
    printf '\nfile: %s (%s)\n' "${SECRETS_FILE}" "$(stat -c '%a' "${SECRETS_FILE}")"
  else
    printf '\nfile: %s (absent)\n' "${SECRETS_FILE}"
  fi
  if [[ ${missing} -gt 0 ]]; then
    printf '%d required key(s) missing\n' "${missing}"
    return 1
  fi
  printf 'all required keys present'
  if [[ ${optional} -gt 0 ]]; then
    printf ' (%d optional not set)' "${optional}"
  fi
  if [[ ${deferred} -gt 0 ]]; then
    printf ' (%d deferred by the provider)' "${deferred}"
  fi
  printf '\n'
  return 0
}

main() {
  case "${1:-}" in
    --check) run_check ;;
    --help | -h) usage ;;
    "") run_interactive ;;
    *)
      printf 'unknown option: %s\n\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
}

# Sourced by the tests to exercise the validators directly; executed normally otherwise.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
