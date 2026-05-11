# Audio model downloads

1. Go to official Microsoft UniLM BEATs repo:
   https://github.com/microsoft/unilm/tree/master/beats

2. Download:
   Fine-tuned BEATs_iter3+ (AS2M) (cpt2)

3. Rename downloaded checkpoint to:
   BEATs_iter3_plus_AS2M_finetuned_cpt2.pt

4. Place it at:
   services/ai/models/audio/beats/BEATs_iter3_plus_AS2M_finetuned_cpt2.pt

5. Optional accuracy ensemble:
   Download Fine-tuned BEATs_iter3+ (AS2M) (cpt1)
   Rename to BEATs_iter3_plus_AS2M_finetuned_cpt1.pt
   Put it in the same directory.

6. Run verification:
   docker compose run --rm ai python scripts/verify_beats_checkpoint.py \
       --model-path /app/models/audio/beats/BEATs_iter3_plus_AS2M_finetuned_cpt2.pt

Important:

- Do not commit large `.pt` files to Git unless repository policy allows it.
- Prefer local volume or Git LFS.
- Do not download from unofficial mirrors.
