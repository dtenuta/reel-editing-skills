#!/bin/zsh
# One-time venvs for the music stage (kept apart from the main faster-whisper venv; needs scipy/librosa).
#   setup_audio.sh              music stage venv  (~/.cache/reel-audio/venv: numpy scipy librosa matplotlib)
#   setup_audio.sh --analysis   reference-style analysis venv for ref_audio_profile.py
#                               (~/.cache/reel-audio/analysis-venv: + torch, demucs, panns_inference, soundfile;
#                               Python 3.11, about 1.5 GB; PANNs CNN14 checkpoints go to ~/panns_data, about 650 MB)
set -e
if [[ $1 == --analysis ]]; then
  V=${ANALYSIS_VENV:-$HOME/.cache/reel-audio/analysis-venv}
  PYB=${PYTHON311:-$(command -v python3.11 || command -v python3)}
  "$PYB" -m venv "$V"
  "$V/bin/pip" -q install "numpy>=2,<2.3" "scipy>=1.13" "librosa>=0.10" "matplotlib>=3.8" "soundfile>=0.12" \
    "torch>=2.2" "torchaudio>=2.2" "demucs==4.1.0" "panns_inference==0.1.1"
  mkdir -p ~/panns_data
  # checkpoints from Zenodo (PANNs authors); curl -C - resumes a truncated download
  for f in "Cnn14_mAP%3D0.431.pth:Cnn14_mAP=0.431.pth" "Cnn14_DecisionLevelMax_mAP%3D0.385.pth:Cnn14_DecisionLevelMax.pth"; do
    src=${f%%:*}; dst=${f##*:}
    [[ -s ~/panns_data/$dst ]] || curl -fL -C - -o ~/panns_data/$dst "https://zenodo.org/record/3987831/files/$src?download=1"
  done
  [[ -s ~/panns_data/class_labels_indices.csv ]] || curl -fL -o ~/panns_data/class_labels_indices.csv \
    "https://raw.githubusercontent.com/qiuqiangkong/audioset_tagging_cnn/master/metadata/class_labels_indices.csv"
  echo "analysis venv ready: $V/bin/python (run ref_audio_profile.py with -I)"
  exit 0
fi
V=${AUDIO_VENV:-$HOME/.cache/reel-audio/venv}
python3 -m venv "$V"
"$V/bin/pip" -q install "numpy>=2" "scipy>=1.13" "librosa>=0.10" "matplotlib>=3.8"
echo "audio venv ready: $V/bin/python"
