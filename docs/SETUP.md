# 📱 Guia de Configuração — pelo celular, sem código

Este guia te leva do zero até o bot funcionando. Tudo pelo celular, sem programar nada.

**Tempo estimado:** 30–45 minutos (a maioria é esperar email de confirmação).

---

## ✅ O que você vai fazer

Você vai criar conta em 5 sites (todos grátis), pegar uma "chave" (tipo senha) em cada um, e colar essas chaves num lugar só no GitHub.

**Checklist:**
- [ ] 1. Criar bot no Telegram (BotFather)
- [ ] 2. Pegar seu chat_id no Telegram
- [ ] 3. Criar conta na Groq (IA do roteiro)
- [ ] 4. Criar conta no Pexels (vídeos)
- [ ] 5. Criar conta no Pixabay (imagens)
- [ ] 6. Criar conta no Cloudflare (storage)
- [ ] 7. Colar tudo nos GitHub Secrets
- [ ] 8. Ligar o bot

---

## 1️⃣ Criar o bot no Telegram

1. Abre o Telegram no celular
2. Na busca, pesquisa: **@BotFather** (é o bot oficial do Telegram)
3. Abre a conversa com ele e aperta **"Iniciar"** (ou `/start`)
4. Manda a mensagem: `/newbot`
5. Ele vai perguntar o **nome** do bot — escolhe qualquer nome (ex: "Meu Gerador de Vídeo")
6. Depois ele pede um **username** que termine com "bot" — ex: `meu_gerador_video_bot`
7. Ele te manda uma mensagem com um código longo tipo:
   ```
   123456789:ABCdef-GHIjklMNOpqrSTUvwxYZ
   ```
   **Esse é o TELEGRAM_BOT_TOKEN. Copia e guarda.** (pode printar a tela pra não perder)

---

## 2️⃣ Pegar seu chat_id

1. No Telegram, pesquisa o nome do bot que você acabou de criar e abre
2. Aperta **"Iniciar"** (ou `/start`)
3. Ele vai responder com uma mensagem contendo seu `chat_id` (um número tipo `123456789`)
   > *Obs: a primeira resposta só vai funcionar depois que o bot estiver ligado no passo 8. Por enquanto, pula esse passo e volta depois de terminar o 7.*

**Alternativa mais rápida:** pesquisa no Telegram `@userinfobot`, inicia a conversa e ele te responde com seu `chat_id` direto.

Anota o número — é o **TELEGRAM_ALLOWED_CHAT_ID**.

---

## 3️⃣ Groq (IA que cria o roteiro)

1. No navegador do celular, abre: **https://console.groq.com**
2. Aperta **"Sign in with Google"** (usa a mesma conta do GitHub pra facilitar)
3. Depois que entrar, abre o menu (≡ no canto) → **"API Keys"**
4. Aperta **"Create API Key"**
5. Dá um nome qualquer (ex: "video-bot")
6. Ele mostra uma chave tipo `gsk_...` — **copia na hora** (só aparece uma vez)
7. **Essa é a GROQ_API_KEY.**

---

## 4️⃣ Pexels (vídeos stock)

1. Abre: **https://www.pexels.com/api/**
2. Aperta **"Get Started"**
3. Cria conta com Google ou email
4. Ele pede uma descrição rápida do uso — escreve "personal video generation tool"
5. Ele te dá uma chave na hora
6. **Essa é a PEXELS_API_KEY.**

---

## 5️⃣ Pixabay (imagens e SFX)

1. Abre: **https://pixabay.com/api/docs/**
2. Aperta **"Register"** (ou entra se já tiver conta)
3. Depois de logado, volta nessa mesma página
4. A chave aparece no próprio texto da página (procura por "Your API key:")
5. **Essa é a PIXABAY_API_KEY.**

---

## 6️⃣ Cloudflare R2 (storage dos vídeos)

Esse é o mais chatinho mas vale porque **é o único com saída de dados grátis pra vídeo**.

1. Abre: **https://dash.cloudflare.com/sign-up**
2. Cria conta grátis com email
3. Confirma email
4. Depois de logado, no menu lateral: **"R2 Object Storage"**
5. Aperta **"Purchase R2"** — é GRÁTIS pra começar, mas ele pede cartão (não cobra enquanto estiver no tier grátis de 10GB). *Alternativa: se não quiser cartão, pula o R2 e deixa o bot mandar o vídeo direto no Telegram (limite de 50MB, o que é mais que suficiente pra vídeos curtos).*
6. Depois de ativar, aperta **"Create bucket"**
7. Nome do bucket: `video-maker-bot` (exato)
8. Região: **Automatic**
9. Com o bucket criado, vai em **"Manage R2 API Tokens"** → **"Create API Token"**
10. Permissão: **"Object Read & Write"**
11. Copia os 3 valores que ele mostra:
    - **Access Key ID** → R2_ACCESS_KEY_ID
    - **Secret Access Key** → R2_SECRET_ACCESS_KEY
    - **Account ID** → R2_ACCOUNT_ID (aparece no canto da página principal do R2)
12. (Opcional) Habilita acesso público no bucket:
    - Clica no bucket → aba **"Settings"** → **"Public access"** → **"Allow Access"**
    - Copia a **Public R2.dev URL** → **R2_PUBLIC_URL**

Se pular o R2, o bot ainda funciona — só fica limitado a vídeos ≤ 50MB (bem o caso de Reels/TikTok de 30s).

---

## 7️⃣ Colar tudo nos GitHub Secrets

Agora você vai pegar todas aquelas chaves que copiou e colar num lugar só.

1. Abre o GitHub no celular: **https://github.com/gustavooliveiraa0511-beep/IA-**
2. Aperta o ícone de menu (⋯ ou hambúrguer) → **"Settings"**
   > Se não achar Settings no mobile, abre no navegador em modo desktop: aperta os 3 pontinhos do navegador → "Ver como desktop"
3. No menu lateral: **"Secrets and variables"** → **"Actions"**
4. Aperta **"New repository secret"**
5. Pra CADA chave abaixo, cria um secret separado com **o nome exatamente igual** e cola o valor:

| Name | Value |
|------|-------|
| `TELEGRAM_BOT_TOKEN` | o token do BotFather (passo 1) |
| `TELEGRAM_ALLOWED_CHAT_ID` | seu chat_id (passo 2) |
| `GROQ_API_KEY` | chave da Groq (passo 3) |
| `PEXELS_API_KEY` | chave da Pexels (passo 4) |
| `PIXABAY_API_KEY` | chave da Pixabay (passo 5) |
| `R2_ACCOUNT_ID` | Account ID da Cloudflare (passo 6) — *opcional* |
| `R2_ACCESS_KEY_ID` | Access Key da R2 — *opcional* |
| `R2_SECRET_ACCESS_KEY` | Secret Key da R2 — *opcional* |
| `R2_BUCKET_NAME` | `video-maker-bot` — *opcional* |
| `R2_PUBLIC_URL` | URL pública do bucket — *opcional* |
| `BOT_GH_TOKEN` | (ver abaixo 👇) |

### Criar o BOT_GH_TOKEN (pro bot disparar GitHub Actions)

1. No GitHub, clica na sua foto (canto superior direito) → **"Settings"**
2. No menu lateral (bem embaixo): **"Developer settings"**
3. **"Personal access tokens"** → **"Fine-grained tokens"**
4. **"Generate new token"**
5. Nome: `bot-video-dispatch`
6. Expiração: **1 year**
7. Repository access: **"Only select repositories"** → escolhe **IA-**
8. Permissões → **"Repository permissions"**:
   - **Actions: Read and write**
   - **Contents: Read**
   - **Metadata: Read** (já vem ligado)
9. Gera o token, copia o valor tipo `github_pat_...`
10. Cria um secret no repo chamado `BOT_GH_TOKEN` com esse valor

---

## 8️⃣ Ligar o bot

1. No seu repositório do GitHub, abre a aba **"Actions"**
2. Se aparecer aviso pra habilitar Actions, aceita
3. No menu esquerdo, acha **"🤖 Bot Telegram (polling)"**
4. Aperta **"Run workflow"** → **"Run workflow"**
5. Em alguns segundos o bot fica online
6. Abre o Telegram, vai no seu bot e manda `/start`

Se aparecer seu chat_id na resposta, **ANOTA** e atualiza o secret `TELEGRAM_ALLOWED_CHAT_ID` com ele (se você não tinha conseguido antes).

---

## 🎬 Usando o bot

No Telegram, manda:

```
/criar nunca desista dos seus sonhos
```

O bot confirma e em 2-5 minutos te manda o vídeo pronto!

Outros comandos:

```
/template motivacional    # troca o estilo
/voz francisca            # troca a voz
/roteiro abundância       # só roteiro, pra aprovar antes
/refazer                  # refaz o último tema
/ajuda                    # lista tudo
```

---

## 🐛 Problemas comuns

- **Bot não responde:** cheque se o workflow "Bot Telegram" tá rodando (verde) na aba Actions
- **"Falha ao iniciar geração":** cheque se `BOT_GH_TOKEN` foi criado com permissão Actions:write
- **Vídeo não chega:** veja os logs do workflow "Gerar Vídeo" na aba Actions — o erro aparece lá
- **Limite de Actions:** 2000 min/mês. Cada vídeo gasta ~5 min. Se estourar, o bot pausa até o mês virar.

---

## 💡 Dicas pra usar bem

- Temas específicos dão roteiros melhores: em vez de "motivação", usa "como superar o medo de começar um negócio"
- Se não gostou, `/refazer` gera variação
- `/roteiro` antes de `/criar` pra aprovar o texto sem gastar os 5 min de render
- Hashtags vêm na descrição do vídeo — só copia e cola quando postar
