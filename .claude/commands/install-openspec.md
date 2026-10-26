---
name: install-openspec
description: Install the OpenSpec CLI locally or globally and optionally
  initialize this project
---
# Install OpenSpec

Ask which package manager to use: npm, Bun, or Yarn. Then ask whether to install OpenSpec globally or in this project. State the exact command that will run and ask for confirmation before running it.

Before installation, verify that Node.js is version 20.19.0 or newer. If it is not, stop and explain that OpenSpec requires Node.js >=20.19.0.

After confirmation:

- npm global: `npm install -g @fission-ai/openspec@latest`
- npm project-local: `npm install -D @fission-ai/openspec@latest`
- Bun global: `bun add -g @fission-ai/openspec@latest`
- Bun project-local: `bun add -D @fission-ai/openspec@latest`
- Yarn global: `yarn global add @fission-ai/openspec@latest`
- Yarn project-local: `yarn add -D @fission-ai/openspec@latest`

Verify a global install with `openspec --version`. Verify a project-local install with the selected package manager's execution command: `npx openspec --version`, `bunx openspec --version`, or `yarn openspec --version`. Explain that upstream workflow skills invoke `openspec` directly, so local installations require the project binary to be on `PATH`.

Then ask whether to initialize OpenSpec in the current project. If confirmed, run `openspec init --tools none` for a global install or the selected package manager's execution command followed by `openspec init --tools none` for a project-local install.

If npm, Bun, and Yarn are all unavailable, ask the user which installation method they want to use. Explain that this installer cannot safely choose an unsupported package manager, but the agent can investigate their requested method.
