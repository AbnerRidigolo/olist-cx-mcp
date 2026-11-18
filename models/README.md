# Modelos

`models/motivo/modelo.joblib`: classificador de motivo (baseline TF-IDF de palavras e caracteres +
regressão logística, 7 classes, scikit-learn 1.9.1), treinado no projeto `fine-tuning-hugging-face`
com rótulos silver gerados por LLM.

Métricas no conjunto de teste de 400 comentários (rótulos de referência gerados por LLM, sem rotulagem
humana), de `fine-tuning-hugging-face/models/baseline_tfidf_lr/gold_relatorio.json`:
acurácia 0,812; F1 macro 0,760; F1 por classe de 0,52 (`outro`) a 0,88 (`elogio`).

Está versionado aqui (2 MB) para que testes, CI e Docker funcionem sem download. Licença: derivado do
dataset da Olist (CC BY-NC-SA 4.0), redistribuído sob a mesma licença, com crédito à Olist.

Quando o BERTimbau for publicado no Hugging Face, ele entra como outra implementação de
`olist_cx_mcp.classificador.Classificador`, sem mudar as ferramentas.
