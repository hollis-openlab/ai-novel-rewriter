# LLM Provider Settings Page Design

> Overrides MASTER.md for Provider Settings

## Layout

```
┌──────────────────────────────────────────────────────────┐
│  LLM Providers                          [+ Add Provider] │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ┌─ Provider Card: OpenAI ───────────────────────────┐   │
│  │                                                    │   │
│  │  OpenAI                              [● Connected] │   │
│  │  GPT-4o                                            │   │
│  │                                                    │   │
│  │  API Key: ••••••••••••sk-7f3B   [Change]           │   │
│  │                                                    │   │
│  │  Rate Limits                                       │   │
│  │  RPM: [  60  ]    TPM: [ 100,000 ]                 │   │
│  │                                                    │   │
│  │  Used For:                                         │   │
│  │  [Analyze ✓] [Rewrite ✓] [Split ○]                │   │
│  │                                                    │   │
│  │  [Test Connection]              [Delete]            │   │
│  └───────────────────────────────────────────────────┘   │
│                                                          │
│  ┌─ Provider Card: Anthropic ────────────────────────┐   │
│  │                                                    │   │
│  │  Anthropic                        [● Connected]    │   │
│  │  Claude Sonnet 4                                   │   │
│  │                                                    │   │
│  │  API Key: ••••••••••••sk-ant-   [Change]           │   │
│  │  ...                                               │   │
│  └───────────────────────────────────────────────────┘   │
│                                                          │
│  ┌─ Provider Card: Ollama ───────────────────────────┐   │
│  │                                                    │   │
│  │  Ollama (Local)                   [○ Disconnected] │   │
│  │  http://localhost:11434                             │   │
│  │  ...                                               │   │
│  └───────────────────────────────────────────────────┘   │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

## Provider Cards

- `bg-white rounded-2xl p-6 shadow-xs border border-border`
- Header row: Provider name (title-2) + status badge
  - Connected: green dot + "Connected" in green
  - Disconnected: gray dot + "Disconnected" in gray
  - Error: red dot + error message
- Model name: `callout text-secondary`
- API Key: masked with dots, last 4 visible, [Change] link
- Rate limits: inline number inputs, compact layout
- "Used For" row: toggle chips showing which stages use this provider
  - Active: `bg-accent/10 text-accent border-accent`
  - Inactive: `bg-subtle text-secondary`
- Test Connection button: secondary style, shows latency result inline
- Delete: text button, `text-error`, requires confirmation modal

## Add Provider Modal

- Modal: `rounded-2xl p-8 max-w-lg`
- Provider type selector: 3 cards (OpenAI / Anthropic / Ollama)
- Based on selection, show relevant fields:
  - OpenAI/Anthropic: API Key input + model name dropdown
  - Ollama: Server URL + model name
- [Test & Save] primary button
