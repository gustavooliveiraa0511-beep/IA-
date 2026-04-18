# 📋 Como criar os 2 workflows do GitHub Actions

Por limitação de permissão do bot de sincronização, os 2 arquivos de workflow precisam ser criados **por você**, direto no GitHub pelo celular. É só copiar e colar — **30 segundos cada**.

## 🔁 Pra cada arquivo, faça isso:

### Arquivo 1: `generate-video.yml`

1. Abre o GitHub no celular: **https://github.com/gustavooliveiraa0511-beep/IA-**
2. Aperta **"Add file"** (ou o ícone `+`) → **"Create new file"**
3. No campo do nome do arquivo, digita **exatamente**:
   ```
   .github/workflows/generate-video.yml
   ```
   *(O GitHub vai criar a pasta automaticamente quando você digita a barra `/`)*
4. Cola o conteúdo do arquivo **`generate-video.yml.txt`** (está nessa mesma pasta)
5. Rola até embaixo → **"Commit changes"** → **"Commit changes"**

### Arquivo 2: `telegram-bot.yml`

Repete o mesmo processo, mas com:
- Nome: `.github/workflows/telegram-bot.yml`
- Conteúdo: o do arquivo `telegram-bot.yml.txt`

## ✅ Pronto

Depois de criar os 2 arquivos, a aba **"Actions"** do seu repositório vai mostrar os 2 workflows disponíveis. Aí é só seguir o passo 8 do `docs/SETUP.md` pra ligar o bot.
