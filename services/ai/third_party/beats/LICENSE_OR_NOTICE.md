# BEATs — Source Attribution and License Notice

The files `BEATs.py` and `Tokenizers.py` in this directory are copied verbatim
from the official Microsoft UniLM repository:

    https://github.com/microsoft/unilm/tree/master/beats

## How to obtain the files

1. Clone or download the UniLM repository:

       git clone --depth 1 https://github.com/microsoft/unilm.git
       cp unilm/beats/BEATs.py       services/ai/third_party/beats/
       cp unilm/beats/Tokenizers.py  services/ai/third_party/beats/

2. Confirm the files are present before running verify_beats_checkpoint.py.

## License

BEATs is released under the MIT License by Microsoft Corporation.
See the original repository for the full license text:

    https://github.com/microsoft/unilm/blob/master/LICENSE

VigilZone does not modify the BEATs source. All intellectual property
remains with the original authors (Sanyuan Chen et al., Microsoft Research).

## Citation

    @article{chen2022beats,
      title     = {BEATs: Audio Pre-Training with Acoustic Tokenizers},
      author    = {Sanyuan Chen and Yu Wu and Chengyi Wang and Shujie Liu
                   and Daniel Povey and Sanjeev Khudanpur and Jinyu Li
                   and Furu Wei},
      journal   = {arXiv preprint arXiv:2212.09058},
      year      = {2022},
    }
