# Product

## Register

product

## Users

Two audiences on the same page: (1) anime/manga fans asking real questions about plot, genres, episode counts; (2) hiring managers/recruiters evaluating this as Sandeep's production-LLM portfolio piece. Both land on the same single-page chat interface — no separate marketing shell.

## Product Purpose

RAG assistant over clean (non-adult) anime/manga metadata. Answers grounded in real synopses/genres/tags with citations and a visible tool-routing decision (semantic search vs. structured filter). Success = a fan gets a correct, cited answer fast, and a recruiter reads the interaction as genuine engineering craft, not a tutorial clone.

## Brand Personality

Focused, precise, quiet-otaku. The anime/manga theming lives in typography (Noto Serif/Sans JP), the 狐 (fox) mark, and color — not in loud decoration, mascots, or playful copy. Confidence through restraint: it should read as a serious tool that happens to love its subject, not a fan-page that happens to have a chatbot.

## Anti-references

- Generic ChatGPT-clone gray box (no visual identity, no craft signal).
- Loud "anime app" tropes: sound effects, cutesy mascots, manga-panel borders, gradient-drenched buttons.
- SaaS-cliché chat widgets (floating bubble launcher, corporate blue, hero-metric stats bar).

## Design Principles

- Restraint carries the theming — one identity (fox mark, JP serif, indigo/orange) expressed consistently, not decorated further.
- The interaction is the pitch. Every improvement should make the actual chat/search experience feel more considered, not add surface decoration.
- Honesty over gloss — this project's story is "I measured and fixed real problems"; the UI shouldn't oversell with fake polish (no skeleton-loader theater, no metrics that aren't real).
- Small surface, no wasted motion — single page, one composer, one scroll region. Every element must earn its place.

## Accessibility & Inclusion

WCAG AA baseline. Existing patterns to preserve: `aria-live` on the message thread, `focus-visible` outlines, 44px minimum tap targets, `prefers-reduced-motion` fallback (already present for bubble rise / typing dots — extend to any new scroll or composer motion).
