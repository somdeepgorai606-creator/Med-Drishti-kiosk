# Med-Drishti Machine Learning & Kaggle Training Guide

This directory contains Jupyter Notebooks and instructions for fine-tuning Med-Drishti's AI models using **Kaggle's free GPU infrastructure** (NVIDIA T4 x2 / P100).

---

## 📁 Directory Overview

| File | Description | Target Model |
| :--- | :--- | :--- |
| `kaggle_train_ocr.ipynb` | Fine-tunes Transformer OCR for medical prescription & handwritten document recognition | `microsoft/trocr-base-stage1` |
| `kaggle_train_triage.ipynb` | Fine-tunes BioBERT / ClinicalBERT for clinical triage and red-flag classification | `emilyalsentzer/Bio_ClinicalBERT` |
| `README.md` | Step-by-step Kaggle training and model deployment guide | Documentation |

---

## 🚀 Step 1: Uploading Notebooks to Kaggle

### Option A: Via Kaggle Web UI (Simplest)
1. Go to [Kaggle Notebooks](https://www.kaggle.com/code).
2. Click **+ New Notebook**.
3. In the top toolbar, click **File -> Import Notebook**.
4. Drag and drop `ml/kaggle_train_ocr.ipynb` or `ml/kaggle_train_triage.ipynb`.
5. Under **Notebook Settings** (right panel):
   - **Accelerator**: Select `GPU T4 x2` or `GPU P100`.
   - **Internet**: Toggle to `ON`.
   - **Persistence**: Toggle `Filesystem ON`.

### Option B: Via Kaggle CLI
1. Install Kaggle CLI:
   ```bash
   pip install kaggle
   ```
2. Download API token (`kaggle.json`) from your Kaggle Account Settings and place it in `~/.kaggle/kaggle.json`.
3. Push kernel to Kaggle:
   ```bash
   kaggle kernels push -p ml/
   ```

---

## 📊 Step 2: Preparing & Attaching Datasets on Kaggle

### For Medical OCR (`kaggle_train_ocr.ipynb`):
- **Primary Dataset:** **RxHandBD** (Handwritten Prescription Word Image Dataset)
  - Contains doctor handwritten prescription words, drug names, dosages, and medical annotations.
  - Attach to Kaggle Notebook via **+ Add Data** -> Search `RxHandBD` or `rxhandbd-handwritten-prescription-word`.
  - Alternatively via Kaggle CLI:
    ```bash
    kaggle datasets download -d rxhandbd-handwritten-prescription-word
    ```
- **Fallback / Secondary Datasets:**
  - IAM Handwriting Dataset (`kaggle datasets download -d nadawi/iam-handwriting-word-database`)
  - Auto-generated synthetic prescription word samples (included in notebook for out-of-the-box testing).

### For Clinical Triage (`kaggle_train_triage.ipynb`):
- Uses Bio-ClinicalBERT pretrained weights from HuggingFace.
- You can upload custom annotated CSV datasets (`symptoms`, `red_flags`, `triage_level`) to Kaggle Datasets and load them into the pandas dataframe.

---

## 💾 Step 3: Training & Exporting Model Weights

1. Click **Run All** in your Kaggle Notebook.
2. Once training completes, the notebook exports the fine-tuned model files into Kaggle's working directory `/kaggle/working/`:
   - `/kaggle/working/med_drishti_trocr_model/`
   - `/kaggle/working/med_drishti_triage_model/`
3. Download the trained weights directory via **Output tab** or Kaggle CLI:
   ```bash
   kaggle kernels output <username>/<kernel-name> -p backend/models/
   ```

---

## 🔌 Step 4: Integrating Fine-Tuned Weights into Med-Drishti Backend

Copy the downloaded model folder into the Med-Drishti backend structure:

```text
d:\Projects\MED-DRISHTI-main\backend\
└── models\
    ├── trocr_medical\       <-- Downloaded from kaggle_train_ocr.ipynb
    │   ├── pytorch_model.bin
    │   ├── config.json
    │   └── preprocessor_config.json
    └── triage_biobert\      <-- Downloaded from kaggle_train_triage.ipynb
        ├── pytorch_model.bin
        ├── config.json
        └── tokenizer.json
```

The backend automatically detects files in `backend/models/` and switches from fallback regex/heuristics to PyTorch fine-tuned model inference!
