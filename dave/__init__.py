"""Dave – Discord-KI-Bot."""

import os as _os

# Rechen-Bibliotheken (numpy/OpenBLAS, onnxruntime, tokenizers) starten sonst so viele Threads, wie der Host
# Kerne hat. In einem Container mit 1-2 gebuchten Kernen drehen die sich gegenseitig die CPU ab und Dave hängt
# (Antworten > 10 s, "Can't keep up"). Muss gesetzt sein, bevor numpy & Co. geladen werden – deshalb hier.
for _var, _val in {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
                   "NUMEXPR_NUM_THREADS": "1", "RAYON_NUM_THREADS": "1", "TOKENIZERS_PARALLELISM": "false"}.items():
    _os.environ.setdefault(_var, _val)
