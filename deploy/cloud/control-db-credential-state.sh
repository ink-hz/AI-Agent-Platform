#!/bin/bash
set -euo pipefail
umask 077

credential_fail() {
  echo "CONTROL_DATABASE_CREDENTIAL_STATE_FAILED" >&2
  exit 1
}

production_roles=(
  platform_control_migrator
  platform_control_app
  platform_directory_worker
  platform_stream_ingest
  platform_audit_append
  platform_control_maintenance
)
preview_roles=(
  platform_control_migrator_preview
  platform_control_app_preview
  platform_directory_worker_preview
  platform_stream_ingest_preview
  platform_audit_append_preview
  platform_control_maintenance_preview
)
production_password_names=(
  control-migrator-password
  control-app-password
  control-directory-worker-password
  control-stream-ingest-password
  control-audit-append-password
  control-maintenance-password
)
preview_password_names=(
  preview-control-migrator-password
  preview-control-app-password
  preview-control-directory-worker-password
  preview-control-stream-ingest-password
  preview-control-audit-append-password
  preview-control-maintenance-password
)
production_dsn_names=(
  control-migrator-database-url
  control-database-url
  control-directory-worker-database-url
  control-stream-ingest-database-url
  control-audit-database-url
  control-maintenance-database-url
)
preview_dsn_names=(
  preview-control-migrator-database-url
  preview-control-database-url
  preview-control-directory-worker-database-url
  preview-control-stream-ingest-database-url
  preview-control-audit-database-url
  preview-control-maintenance-database-url
)
all_roles=("${production_roles[@]}" "${preview_roles[@]}")
all_password_names=(
  "${production_password_names[@]}" "${preview_password_names[@]}"
)
all_dsn_names=("${production_dsn_names[@]}" "${preview_dsn_names[@]}")
all_database_names=(
  agent_platform_control
  agent_platform_control
  agent_platform_control
  agent_platform_control
  agent_platform_control
  agent_platform_control
  agent_platform_control_preview
  agent_platform_control_preview
  agent_platform_control_preview
  agent_platform_control_preview
  agent_platform_control_preview
  agent_platform_control_preview
)

file_mode() {
  local path="$1"
  if /usr/bin/stat -c '%a' "$path" >/dev/null 2>&1; then
    /usr/bin/stat -c '%a' "$path"
  else
    /usr/bin/stat -f '%Lp' "$path"
  fi
}

valid_private_file() {
  local path="$1"
  [[ -f "$path" && ! -L "$path" && "$(file_mode "$path")" == "600" ]]
}

read_one_line() {
  local path="$1"
  [[ "$(/usr/bin/wc -l < "$path" | /usr/bin/tr -d ' ')" == "1" ]] || return 1
  /usr/bin/tr -d '\n' < "$path"
}

valid_password_file() {
  local path="$1"
  local value
  valid_private_file "$path" || return 1
  value="$(read_one_line "$path")" || return 1
  [[ "$value" =~ ^[0-9a-f]{64}$ ]]
}

valid_dsn_file() {
  local path="$1"
  local role="$2"
  local password="$3"
  local database="$4"
  local expected actual
  valid_private_file "$path" || return 1
  expected="postgresql://${role}:${password}@platform-postgres:5432/${database}"
  actual="$(read_one_line "$path")" || return 1
  [[ "$actual" == "$expected" ]]
}

valid_isolated_layout_at() {
  local directory="$1"
  local index password seen_password
  declare -a seen_passwords=()
  for index in "${!all_roles[@]}"; do
    valid_password_file "$directory/${all_password_names[$index]}" || return 1
    password="$(read_one_line "$directory/${all_password_names[$index]}")"
    if [[ "${#seen_passwords[@]}" -gt 0 ]]; then
      for seen_password in "${seen_passwords[@]}"; do
        [[ "$password" != "$seen_password" ]] || return 1
      done
    fi
    valid_dsn_file \
      "$directory/${all_dsn_names[$index]}" \
      "${all_roles[$index]}" "$password" "${all_database_names[$index]}" \
      || return 1
    seen_passwords+=("$password")
  done
}

valid_legacy_layout_at() {
  local directory="$1"
  local index password
  for index in "${!production_roles[@]}"; do
    valid_password_file "$directory/${production_password_names[$index]}" \
      || return 1
    [[ ! -e "$directory/${preview_password_names[$index]}" ]] || return 1
    password="$(read_one_line "$directory/${production_password_names[$index]}")"
    valid_dsn_file \
      "$directory/${production_dsn_names[$index]}" \
      "${production_roles[$index]}" "$password" agent_platform_control \
      || return 1
    valid_dsn_file \
      "$directory/${preview_dsn_names[$index]}" \
      "${production_roles[$index]}" "$password" agent_platform_control_preview \
      || return 1
  done
}

no_control_credentials_at() {
  local directory="$1"
  local name
  for name in "${all_password_names[@]}" "${all_dsn_names[@]}"; do
    [[ ! -e "$directory/$name" ]] || return 1
  done
}

state_contents() {
  local state_file="$1"
  valid_private_file "$state_file" || return 1
  /bin/cat "$state_file"
}

classify_layout() {
  local private_path="$1"
  local state_file="$private_path/.control-database-credentials-v2.state"
  local work_path="$private_path/.control-database-credentials-v2"
  local content origin
  [[ -d "$private_path" && ! -L "$private_path" ]] || return 1
  if [[ -e "$state_file" ]]; then
    content="$(state_contents "$state_file")" || return 1
    if [[ "$content" == $'version=2\nstatus=complete' ]]; then
      valid_isolated_layout_at "$private_path" || return 1
      echo complete
      return
    fi
    for origin in fresh legacy-shared isolated-unmarked; do
      if [[ "$content" == $'version=2\nstatus=rotating\norigin='"$origin" ]]; then
        if [[ -e "$work_path" ]]; then
          [[ -d "$work_path" && ! -L "$work_path" ]] || return 1
        fi
        echo "rotating:$origin"
        return
      fi
    done
    return 1
  fi
  [[ ! -e "$work_path" ]] || return 1
  if no_control_credentials_at "$private_path"; then
    echo fresh
  elif valid_legacy_layout_at "$private_path"; then
    echo legacy-shared
  elif valid_isolated_layout_at "$private_path"; then
    echo isolated-unmarked
  else
    return 1
  fi
}

atomic_write() {
  local target="$1"
  local value="$2"
  local temporary="${target}.next.$$"
  /usr/bin/printf '%s\n' "$value" > "$temporary"
  /bin/chmod 600 "$temporary"
  /bin/mv -f "$temporary" "$target"
}

prepare_candidates() {
  local private_path="$1"
  local requested_origin="$2"
  local state_file="$private_path/.control-database-credentials-v2.state"
  local work_path="$private_path/.control-database-credentials-v2"
  local classification origin index candidate existing old_password
  local duplicate candidate_password
  declare -a candidates=()
  classification="$(classify_layout "$private_path")" || return 1
  if [[ "$requested_origin" == "complete" && "$classification" == "complete" ]]; then
    echo complete
    return
  fi
  case "$classification" in
    fresh|legacy-shared|isolated-unmarked)
      origin="$classification"
      [[ "$requested_origin" == "$origin" ]] || return 1
      atomic_write "$state_file" $'version=2\nstatus=rotating\norigin='"$origin"
      /bin/mkdir -m 700 "$work_path"
      ;;
    rotating:*)
      origin="${classification#rotating:}"
      [[ "$requested_origin" == "$origin" ]] || return 1
      if [[ ! -e "$work_path" ]]; then
        /bin/mkdir -m 700 "$work_path"
      fi
      [[ -d "$work_path" && ! -L "$work_path" ]] || return 1
      ;;
    *)
      return 1
      ;;
  esac

  for index in "${!all_password_names[@]}"; do
    candidate="$work_path/${all_password_names[$index]}"
    if [[ -e "$candidate" ]]; then
      valid_password_file "$candidate" || return 1
    else
      while true; do
        existing="$(/usr/bin/openssl rand -hex 32)"
        [[ "$existing" =~ ^[0-9a-f]{64}$ ]] || return 1
        if [[ "$index" -lt 6 && "$origin" != "fresh" ]]; then
          old_password="$(read_one_line \
            "$private_path/${production_password_names[$index]}")" || return 1
          [[ "$existing" != "$old_password" ]] || continue
        fi
        duplicate=0
        if [[ "${#candidates[@]}" -gt 0 ]]; then
          for candidate_password in "${candidates[@]}"; do
            if [[ "$existing" == "$candidate_password" ]]; then
              duplicate=1
            fi
          done
        fi
        [[ "$duplicate" -eq 0 ]] || continue
        atomic_write "$candidate" "$existing"
        break
      done
    fi
    candidates+=("$(read_one_line "$candidate")")
  done
  [[ "${#candidates[@]}" -eq 12 ]] || return 1
  [[ "${#candidates[@]}" -eq "$(/usr/bin/printf '%s\n' "${candidates[@]}" | /usr/bin/sort -u | /usr/bin/wc -l | /usr/bin/tr -d ' ')" ]] \
    || return 1
  for index in "${!all_roles[@]}"; do
    atomic_write \
      "$work_path/${all_dsn_names[$index]}" \
      "postgresql://${all_roles[$index]}:${candidates[$index]}@platform-postgres:5432/${all_database_names[$index]}"
  done
  valid_isolated_layout_at "$work_path"
}

publish_candidates() {
  local private_path="$1"
  local work_path="$private_path/.control-database-credentials-v2"
  local classification name source target temporary
  classification="$(classify_layout "$private_path")" || return 1
  [[ "$classification" == rotating:* ]] || return 1
  valid_isolated_layout_at "$work_path" || return 1
  for name in "${all_password_names[@]}" "${all_dsn_names[@]}"; do
    source="$work_path/$name"
    target="$private_path/$name"
    temporary="${target}.next.$$"
    /bin/cp "$source" "$temporary"
    /bin/chmod 600 "$temporary"
    /bin/mv -f "$temporary" "$target"
  done
  valid_isolated_layout_at "$private_path"
}

complete_rotation() {
  local private_path="$1"
  local state_file="$private_path/.control-database-credentials-v2.state"
  local classification
  classification="$(classify_layout "$private_path")" || return 1
  [[ "$classification" == rotating:* ]] || return 1
  valid_isolated_layout_at "$private_path" || return 1
  atomic_write "$state_file" $'version=2\nstatus=complete'
}

[[ $# -ge 2 && $# -le 3 ]] || credential_fail
command_name="$1"
private_path="$2"
case "$command_name" in
  classify)
    [[ $# -eq 2 ]] || credential_fail
    classify_layout "$private_path" || credential_fail
    ;;
  prepare)
    [[ $# -eq 3 ]] || credential_fail
    prepare_candidates "$private_path" "$3" || credential_fail
    ;;
  publish)
    [[ $# -eq 2 ]] || credential_fail
    publish_candidates "$private_path" || credential_fail
    ;;
  complete)
    [[ $# -eq 2 ]] || credential_fail
    complete_rotation "$private_path" || credential_fail
    ;;
  *)
    credential_fail
    ;;
esac
