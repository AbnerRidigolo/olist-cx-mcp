# Modelos

`models/motivo/modelo.joblib`: classificador de motivo (baseline TF-IDF de palavras e caracteres +
regressão logística, 7 classes, scikit-learn 1.9.1), treinado no projeto `fine-tuning-hugging-face`
com rótulos silver gerados por LLM. F1 por classe no conjunto de teste (rótulos de referência de LLM):
de 0,77 a 0,84 nas classes principais.

Está versionado aqui (2 MB) para que testes, CI e Docker funcionem sem download. Licença: derivado do
dataset da Olist (CC BY-NC-SA 4.0), redistribuído sob a mesma licença, com crédito à Olist.

Quando o BERTimbau for publicado no Hugging Face, ele entra como outra implementação de
`olist_cx_mcp.classificador.Classificador`, sem mudar as ferramentas.
