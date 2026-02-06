#!/bin/bash
# =============================================================================
# macOS System Log Collector for Telegraf
# =============================================================================
#
# This script collects macOS unified logs and outputs them in InfluxDB line
# protocol format for ingestion by Telegraf's exec input plugin.
#
# Usage:
#   ./macos_log.sh [OPTIONS]
#
# Options:
#   --lookback DURATION   How far back to look (default: 10s)
#   --level LEVEL         Minimum log level: debug, info, default, error, fault
#   --subsystem PATTERN   Filter by subsystem (e.g., com.apple.*)
#   --process PATTERN     Filter by process name
#   --predicate PRED      Custom NSPredicate filter
#
# Output Format (InfluxDB Line Protocol):
#   macos_log,level=error,subsystem=com.apple.foo,process=bar message="..." timestamp
#
# =============================================================================

set -euo pipefail

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

# Default lookback period (should match Telegraf exec interval)
LOOKBACK="${MACOS_LOG_LOOKBACK:-10s}"

# Minimum log level to collect
# Options: debug, info, default, error, fault
MIN_LEVEL="${MACOS_LOG_LEVEL:-default}"

# Maximum number of log entries per collection (prevent overwhelming output)
MAX_ENTRIES="${MACOS_LOG_MAX_ENTRIES:-100}"

# Subsystem filter (empty = all subsystems)
SUBSYSTEM_FILTER="${MACOS_LOG_SUBSYSTEM:-}"

# Process filter (empty = all processes)
PROCESS_FILTER="${MACOS_LOG_PROCESS:-}"

# Custom predicate filter
CUSTOM_PREDICATE="${MACOS_LOG_PREDICATE:-}"

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

# Escape special characters for InfluxDB line protocol
escape_field_value() {
    local value="$1"
    # Escape backslashes first, then double quotes
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    # Remove or escape newlines
    value="${value//$'\n'/\\n}"
    value="${value//$'\r'/}"
    echo "$value"
}

# Escape tag values (different escaping rules)
escape_tag_value() {
    local value="$1"
    # Escape commas, equals, and spaces in tags
    value="${value//,/\\,}"
    value="${value//=/\\=}"
    value="${value// /\\ }"
    echo "$value"
}

# Convert macOS log level to numeric severity
level_to_severity() {
    case "$1" in
        Fault|fault)     echo "0" ;;
        Error|error)     echo "1" ;;
        Default|default) echo "2" ;;
        Info|info)       echo "3" ;;
        Debug|debug)     echo "4" ;;
        *)               echo "2" ;;
    esac
}

# -----------------------------------------------------------------------------
# Build Log Command
# -----------------------------------------------------------------------------

build_log_command() {
    local cmd=("log" "show" "--last" "$LOOKBACK" "--style" "json")

    # Add level filter
    case "$MIN_LEVEL" in
        debug)   cmd+=("--debug") ;;
        info)    cmd+=("--info") ;;
        default) ;; # default is the... default
        error)   cmd+=("--predicate" "messageType == error OR messageType == fault") ;;
        fault)   cmd+=("--predicate" "messageType == fault") ;;
    esac

    # Add subsystem filter
    if [[ -n "$SUBSYSTEM_FILTER" ]]; then
        cmd+=("--predicate" "subsystem CONTAINS '$SUBSYSTEM_FILTER'")
    fi

    # Add process filter
    if [[ -n "$PROCESS_FILTER" ]]; then
        cmd+=("--predicate" "processImagePath CONTAINS '$PROCESS_FILTER'")
    fi

    # Add custom predicate
    if [[ -n "$CUSTOM_PREDICATE" ]]; then
        cmd+=("--predicate" "$CUSTOM_PREDICATE")
    fi

    echo "${cmd[@]}"
}

# -----------------------------------------------------------------------------
# Main Collection Logic
# -----------------------------------------------------------------------------

collect_logs() {
    local count=0
    local current_time_ns
    current_time_ns=$(date +%s)000000000

    # Build and execute log command, parse JSON output
    # Note: macOS 'log show --style json' outputs newline-delimited JSON objects
    eval "$(build_log_command)" 2>/dev/null | while IFS= read -r line; do
        # Skip empty lines
        [[ -z "$line" ]] && continue

        # Limit number of entries
        if [[ $count -ge $MAX_ENTRIES ]]; then
            break
        fi

        # Parse JSON fields using jq if available, otherwise use grep/sed
        if command -v jq &>/dev/null; then
            # Extract fields using jq
            local timestamp message level subsystem process category

            timestamp=$(echo "$line" | jq -r '.timestamp // empty' 2>/dev/null) || continue
            message=$(echo "$line" | jq -r '.eventMessage // .composedMessage // ""' 2>/dev/null)
            level=$(echo "$line" | jq -r '.messageType // "default"' 2>/dev/null)
            subsystem=$(echo "$line" | jq -r '.subsystem // "unknown"' 2>/dev/null)
            process=$(echo "$line" | jq -r '.processImagePath // "unknown"' 2>/dev/null)
            category=$(echo "$line" | jq -r '.category // ""' 2>/dev/null)

            # Skip if no message
            [[ -z "$message" ]] && continue

            # Extract process name from path
            process=$(basename "$process" 2>/dev/null || echo "$process")

            # Escape values
            local escaped_message escaped_subsystem escaped_process escaped_category
            escaped_message=$(escape_field_value "$message")
            escaped_subsystem=$(escape_tag_value "$subsystem")
            escaped_process=$(escape_tag_value "$process")
            escaped_category=$(escape_tag_value "$category")

            # Get severity level
            local severity
            severity=$(level_to_severity "$level")

            # Convert timestamp to nanoseconds if needed
            local timestamp_ns
            if [[ "$timestamp" =~ ^[0-9]+\.[0-9]+$ ]]; then
                # Already in seconds.fraction format
                timestamp_ns=$(echo "$timestamp" | awk '{printf "%.0f", $1 * 1000000000}')
            else
                # Use current time if timestamp parsing fails
                timestamp_ns=$current_time_ns
            fi

            # Output in InfluxDB line protocol format
            # Format: measurement,tag1=val1,tag2=val2 field1="val1",field2=val2 timestamp
            echo "macos_log,level=$level,severity=$severity,subsystem=$escaped_subsystem,process=$escaped_process,category=$escaped_category message=\"$escaped_message\" $timestamp_ns"

            ((count++))
        else
            # Fallback: output raw log line as message (less structured)
            # This path is used if jq is not installed
            local escaped_line
            escaped_line=$(escape_field_value "$line")
            echo "macos_log,level=unknown,subsystem=unknown,process=unknown message=\"$escaped_line\" $current_time_ns"
            ((count++))
        fi
    done
}

# -----------------------------------------------------------------------------
# Entry Point
# -----------------------------------------------------------------------------

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --lookback)
            LOOKBACK="$2"
            shift 2
            ;;
        --level)
            MIN_LEVEL="$2"
            shift 2
            ;;
        --subsystem)
            SUBSYSTEM_FILTER="$2"
            shift 2
            ;;
        --process)
            PROCESS_FILTER="$2"
            shift 2
            ;;
        --predicate)
            CUSTOM_PREDICATE="$2"
            shift 2
            ;;
        --help|-h)
            head -30 "$0" | tail -n +2 | sed 's/^# //' | sed 's/^#//'
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# Run collection
collect_logs
