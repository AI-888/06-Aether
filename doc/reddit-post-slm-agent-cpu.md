# [Project] I built an AI Agent that runs entirely on CPU with a 1.5B parameter model — here's what I learned

**TL;DR:** I built an intelligent ops agent using a 1.5B model (Qwen2.5:1.5b) that runs on CPU-only machines. It uses RAG + Rerank + structured Skills to achieve usable accuracy without any GPU. Here's the architecture breakdown.

---

## The Problem

I work in private cloud operations. Our customers deploy on-premises — **no public internet, no GPU, no cloud API access**. But they still need intelligent troubleshooting.

- 🚨 **"Livestream debugging"** — Experts remotely guide field engineers step by step. Slow, expensive, knowledge never captured
- 📚 **Documentation maze** — Hundreds of docs, nobody finds the right page when things break
- 💻 **Zero GPU budget** — Not every customer has GPUs, but every customer needs support

> **How do you build an accurate, low-latency AI agent on CPU-only hardware?**

---

## Why Small Language Models

This isn't about using a "worse" GPT-4. SLMs are a different paradigm:

| Dimension | LLM Approach | SLM + System Design |
|-----------|-------------|---------------------|
| **Philosophy** | One model does everything | Model handles language; system handles knowledge + execution |
| **Knowledge** | Baked into parameters | Retrieved from vector DB (RAG) |
| **Cost** | $$$$ per query | Runs on a $200 mini PC |

The key insight: **don't make the model smarter — make the system smarter.**

---

## The Model Stack

Everything runs locally. Zero external API calls.

| Component | Model | Role |
|-----------|-------|------|
| **Main LLM** | `Qwen2.5:1.5b` | Intent understanding, response generation |
| **Embedding** | `bge-large-zh-v1.5` | Text → vector for semantic search |
| **Reranker** | `bge-reranker-v2-m3` | CrossEncoder re-ranking |

Runs in 4GB RAM, ~1-2s per response on CPU.

---

## Key Innovation #1: Rerank Makes SLMs Faster

Adding Rerank **actually made the system faster**, not slower.

Traditional RAG feeds Top-K docs to LLM. With Rerank, we filter to Top-2 high-quality docs first. For SLMs on CPU:

- **Less context = dramatically faster inference** (scales super-linearly with context length)
- **Better context = fewer hallucinations** (SLMs are very sensitive to noise)
- **Net result: 40-60% faster end-to-end**

**Rerank latency:** ~100ms. **Inference time saved:** 500-2000ms. No-brainer.

---

## Key Innovation #2: Tiered Intent Routing

Not every request needs the LLM. A two-phase routing system handles requests at the cheapest level:

```
User Request
    │
    ▼
Phase 1: Rule Engine (~1ms)
  Pre-compiled regex: "check pod" → check_pod_status skill
    │ No match
    ▼
Phase 2: LLM Classifier (~500ms)
  Classification ONLY — no generation, no reasoning
    │
    ▼
Route: Type A (Knowledge QA) → RAG pipeline
       Type D (Operations)   → Skill execution
```

The LLM classifier receives only the skill name list and outputs a single skill name. **80%+ of requests** resolved by rules in **< 5ms**.

---

## Key Innovation #3: From Tools to Structured Skills (SOP)

Traditional agents let the LLM plan tool execution. This falls apart with a 1.5B model. Our approach: **pre-defined playbooks** where the SLM only handles language understanding.

**Atomic Skill** = single tool wrapper, no LLM involved.
**SOP Skill** = chain of Atomic Skills + scoped LLM calls:

```yaml
skill:
  name: resolve_and_get_rocketmq_pods
  type: sop
  steps:
  - id: resolve_component
    type: llm              # LLM does ONE thing: extract params
    prompt: |
      Extract fields from user input. Output JSON ONLY:
      {"namespace":"","component_keyword":"","exclude_keywords":""}

  - id: get_pods
    type: skill            # Atomic Skill, no LLM
    skill: get_rocketmq_pods
    input:
      namespace: "{{resolve_component.namespace}}"
      component_keyword: "{{resolve_component.component_keyword}}"
```

Each LLM step receives **ONLY the context it needs** — not the entire history. This is what makes SLM execution possible.

---

## Key Innovation #4: LoRA Fine-Tuning on Consumer Hardware

We turned a generic Qwen2.5:1.5b into a **RocketMQ operations expert** using LoRA. The entire pipeline runs on a MacBook Pro — no cloud GPU.

```
Data Prep (70% of effort) → LoRA Training (<1% params) → Merge → GGUF q4_k_m → Ollama
```

Key hyperparameters: `rank=8, alpha=16, lr=2e-4, epochs=3`. Final model: **~1GB**, runs on CPU.

**Before vs. After:**

| Query | Base Model | Fine-tuned |
|-------|-----------|------------|
| "Broker won't start" | Generic: check logs | Specific: check `broker.log`, port 10911, disk > 90% |
| "Consumer lag" | Vague: "check consumer" | Specific: `mqadmin consumerProgress`, check Diff field |

---

## Real-World Performance

| Metric | Value |
|--------|-------|
| **End-to-end response** | 1-3s (CPU only) |
| **Full RAG pipeline** | ~200ms |
| **Model memory** | ~2GB (quantized) |
| **Throughput** | ~5 queries/sec |

Runs **offline, on-premises, zero API cost.**

---

## The Takeaway

1. **A 1.5B model on CPU is enough** — if you design the system right
2. **RAG + Rerank > bigger model** — retrieve and filter, don't memorize
3. **Structured Skills > free-form tool use** — don't let the SLM improvise
4. **Tiered routing saves 80% of compute** — most requests don't need the LLM
5. **LoRA on consumer hardware** — domain expertise in hours, not weeks

The future of agentic AI isn't bigger models — it's **smarter systems with smaller models.**

---

*Happy to answer questions about the architecture, training pipeline, or deployment!*
