#!/bin/sh
set -eu

if [ "${AI_DOWNLOAD_MODELS_ON_START:-1}" = "1" ]; then
    beats_model_path="${AI_BEATS_MODEL_PATH:-/app/models/audio/beats/BEATs_iter3_plus_AS2M_finetuned_cpt2.pt}"
    beats_src_dir="${AI_BEATS_SRC_DIR:-/app/third_party/beats}"

    echo "Preparing BEATs audio assets..."
    if ! python scripts/download_beats.py --dest "$beats_model_path" --beats-src "$beats_src_dir"; then
        if [ "${AI_REQUIRE_AUDIO_MODEL:-1}" = "1" ]; then
            echo "BEATs asset provisioning failed and AI_REQUIRE_AUDIO_MODEL=1; refusing to start." >&2
            exit 1
        fi
        echo "WARNING: BEATs asset provisioning failed; continuing with audio lane unavailable." >&2
    fi
fi

exec "$@"
