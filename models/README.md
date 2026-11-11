# Modelos

`models/motivo/modelo.joblib`: classificador de motivo (baseline TF-IDF + regressão logística,
7 classes) treinado no projeto `fine-tuning-hugging-face`. Não vai para o git; copie com:

```powershell
New-Item -ItemType Directory -Force models\motivo
Copy-Item ..\fine-tuning-hugging-face\models\baseline_tfidf_lr\modelo.joblib models\motivo\
```

Quando o BERTimbau for publicado no Hugging Face, ele entra por trás da mesma interface
(`olist_cx_mcp.classificador`), sem mudar as ferramentas.
