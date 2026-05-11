# BEATs Audio Model Checkpoints

Place checkpoint files in this directory before starting the AI service.

## Required file

```
BEATs_iter3_plus_AS2M_finetuned_cpt2.pt
```

## Optional (ensemble accuracy mode)

```
BEATs_iter3_plus_AS2M_finetuned_cpt1.pt
```

## Download instructions

1. Go to the official Microsoft UniLM BEATs page:
   https://github.com/microsoft/unilm/tree/master/beats

2. Download **Fine-tuned BEATs_iter3+ (AS2M) (cpt2)** from the table.

3. Rename the downloaded file to:
   `BEATs_iter3_plus_AS2M_finetuned_cpt2.pt`

4. Copy it into this directory:
   `services/ai/models/audio/beats/BEATs_iter3_plus_AS2M_finetuned_cpt2.pt`

5. (Optional) Download **(cpt1)** and rename to:
   `BEATs_iter3_plus_AS2M_finetuned_cpt1.pt`

## Verify the checkpoint

After copying, run the verification script:

```bash
# Local (development)
python services/ai/scripts/verify_beats_checkpoint.py \
    --model-path services/ai/models/audio/beats/BEATs_iter3_plus_AS2M_finetuned_cpt2.pt \
    --beats-src services/ai/third_party/beats

# Docker
docker compose run --rm ai python scripts/verify_beats_checkpoint.py \
    --model-path /app/models/audio/beats/BEATs_iter3_plus_AS2M_finetuned_cpt2.pt
```

## Important notes

- Do **not** commit the `.pt` files to Git (they are ~340 MB each).
  They are already excluded by `.gitignore`.
- Do **not** download from unofficial mirrors.
- The model requires Python 3.10+, PyTorch 2.0+, and is CPU-compatible.
