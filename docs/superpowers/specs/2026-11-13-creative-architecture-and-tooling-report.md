# Creative Architecture, Tooling Gap Analysis, and Generative Quota Strategy Report

**Date:** 2026-11-16  
**Project:** `media-studio`  
**Target:** Higgsfield-Class & Remotion-Class Creative Motion Architecture with Zero-Cost Google Flow Integration  

---

## Executive Summary

This report establishes the comprehensive architectural roadmap for `media-studio`'s promotional video pipeline, evaluates comparative insights from **Higgsfield AI** and **Remotion**, documents empirical fixes for active rendering and color-space defects, and defines a robust multi-account generative quota strategy powered by **Google Flow** and **Antigravity**.

### Core Strategic Decisions
1. **Preserve Antigravity for Agent Intelligence (100% Coding / Reasoning):**
   - Live inspection of Google Cloud Code endpoints confirmed that `gemini-3.1-flash-image` shares the exact same quota pool as Gemini conversational and coding models.
   - To prevent agent reasoning degradation or rate-limiting during coding sessions, **Antigravity is strictly preserved for agent orchestration and code generation**. All media generation is routed through Google Flow.
2. **Zero-Credit Image Generation via Google Flow (`Nano Banana Pro`):**
   - Verified directly from the Google Flow UI: generating images with **`Nano Banana Pro`**, **`Nano Banana 2 Lite`**, or **`Nano Banana 2.1`** consumes **0 credits** across both Free and Pro tiers.
   - All storyboards, character references, and B-roll stills will be produced via Google Flow's Nano Banana Pro at zero credit expenditure.
3. **Calibrated Video Model Selection (Pre-Dec 5 Deprecation Alignment):**
   - Deprecation notice confirmed in Google Flow: `Omni 1.1 Flash`, `Veo 3.1 - Fast`, and `Veo 3.1 - Quality` will be discontinued on **December 5**.
   - Pipeline models are strictly locked to the surviving, actively maintained generation:
     - **`Veo 3.1 - Lite` (10 credits @ 720p 8s)**: Primary cinematic video engine (half the cost of Veo Fast, ideal for camera moves and frame-to-video).
     - **`Omni 1.2 Flash` (24 credits @ 720p 8s)**: Secondary multimodal video engine (fast dynamic motion, native audio sync).
4. **Waterfall Account Rotation & Credit Preservation:**
   - Multi-account rotation prioritizes **Free Tier Daily Quota** -> **Pro Tier Daily Allowance** -> **Pro 1000 Monthly Credit Bank (Locked by Default)**.
   - Predictive cost checking ensures accounts with insufficient daily balances are bypassed before submitting jobs, keeping the 1000 Pro monthly credits pristine.
5. **Remotion Re-Evaluation (Port the Helpers, Not the Framework):**
   - Remotion remains rejected as an npm dependency (commercial licensing restrictions, 1.5+ GB node_modules overhead).
   - `media-studio` ports Remotion's pure mathematical spring physics (`spring()`) and timing interpolation directly into Playwright `html_motion.py` and `effectcraft.py` keyframe compilers.

---

## 1. Google Flow Model Catalog & Verified Credit Cost Matrix

Based on live cross-checks of the Google Flow (`labs.google/fx/tools/flow`) interface:

### 1.1 Generative Model Matrix & Official Costs

| Media Type | Model Label in Google Flow | Wire / API Model Key | Resolution & Duration | Cost per Batch (x1) | Lifecycle Status | Optimal Use Case |
|---|---|---|---|---|---|---|
| **Image** | **`Nano Banana Pro`** | `GEM_PIX_2` | 16:9, 9:16, 1:1, 4:3 | **0 credits** | Active | **Primary Image Engine**: High-fidelity stills, character references, product staging. |
| **Image** | `Nano Banana 2.1` | `BELUGA` | 16:9, 9:16, 1:1, 4:3 | **0 credits** | Active | Alternative style and lighting rendering. |
| **Image** | `Nano Banana 2 Lite` | `HARBOR_SEAL` | 16:9, 9:16, 1:1, 4:3 | **0 credits** | Active | Rapid storyboard drafts. |
| **Video** | **`Veo 3.1 - Lite`** | `veo_3_1_i2v_lite` | 720p / 360p, 8s | **10 credits** | Active | **Primary Video Engine**: Smooth camera pans, cinematic product reveals, frame-to-video. |
| **Video** | **`Omni 1.2 Flash`** | `abra_t2v_10s` | 720p / 360p, 8s | **24 credits** | Active | Fast multimodal action, dynamic B-roll, complex character animation. |
| **Video** | `Veo 3.1 - Fast` | `veo_3_1_i2v_s_fast` | 720p, 8s | 20 credits | **Deprecated Dec 5** | Phased out; do not use in new pipelines. |
| **Video** | `Veo 3.1 - Quality` | `veo_3_1_i2v_quality`| 720p / 1080p, 8s | 30+ credits | **Deprecated Dec 5** | Phased out; do not use in new pipelines. |
| **Video** | `Omni 1.1 Flash` | `abra_t2v_8s` | 720p, 8s | 20 credits | **Deprecated Dec 5** | Replaced by Omni 1.2 Flash. |

*Batch Multiplier:* Batch selections multiply base credit cost linearly (`x1 = 1x`, `x2 = 2x`, `x3 = 3x`, `x4 = 4x`).

---

## 2. Multi-Account Rotation & Credit Ledger Architecture

### 2.1 Account Hierarchy & Allocation Tiers

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        Generative Media Request                        │
│            e.g. Veo 3.1 - Lite, 720p, 8s, x1 (Cost: 10 Credits)        │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
        ┌──────────────────────────────────────────────────────┐
        │  Check Free Accounts: Any Free account with Daily>=10?│
        └──────────────┬────────────────────────┬──────────────┘
                  YES  │                        │  ALL EXHAUSTED
                       ▼                        ▼
        ┌─────────────────────────┐  ┌───────────────────────────────────┐
        │  Use Free Daily Quota   │  │ Check Pro Accounts: Daily >= 10?  │
        │  (Preserves Pro bank)   │  └─────────┬─────────────────┬────────┘
        └─────────────────────────┘       YES  │                 │  ALL EXHAUSTED
                                               ▼                 ▼
                                    ┌────────────────────┐  ┌─────────────┐
                                    │ Use Pro Daily Pool │  │ Pro Monthly │
                                    │ (Preserves bank)   │  │ 1000 Bank   │
                                    └────────────────────┘  │  (LOCKED)   │
                                                            └─────────────┘
```

1. **Free Account Daily Quota (Priority 1):** Free tier accounts receive daily credits resetting every 24 hours (UTC midnight). Because these credits expire if unused (*use-it-or-lose-it*), they are exhausted first.
2. **Pro Account Daily Allowance (Priority 2):** When free accounts are exhausted, the daily allowance slice of Google AI Pro accounts is utilized.
3. **Pro Account 1000 Monthly Bank (Priority 3 - Locked):** The 1,000 monthly credit allocation in Google AI Pro accounts is maintained as an emergency reserve. It is locked by default (`monthly_locked: true`) and accessed only if `--allow-monthly-pool` is explicitly passed.

### 2.2 Credit Meter & Persistent Ledger (`~/.local/state/media-studio/flow-credits.json`)

The ledger records every generation transaction with atomic file locking (`fcntl`):
```json
{
  "cost_rules": {
    "image": {
      "nano-banana-pro": 0,
      "nano-banana-2.1": 0,
      "nano-banana-2-lite": 0
    },
    "video": {
      "veo-3.1-lite": 10,
      "omni-1.2-flash-8s": 24,
      "veo-3.1-fast": 20
    }
  },
  "accounts": [
    {
      "id": "acc-free-01",
      "email": "creator.free1@gmail.com",
      "tier": "free",
      "daily_credit_limit": 60,
      "daily_credit_spent": 20,
      "monthly_bank_limit": 0,
      "monthly_bank_spent": 0,
      "last_reset": "2026-11-16T00:00:00Z"
    },
    {
      "id": "acc-pro-01",
      "email": "creator.pro@gmail.com",
      "tier": "pro",
      "daily_credit_limit": 60,
      "daily_credit_spent": 0,
      "monthly_bank_limit": 1000,
      "monthly_bank_spent": 0,
      "monthly_locked": true,
      "last_reset": "2026-11-16T00:00:00Z"
    }
  ]
}
```

### 2.3 Pre-Flight Balance Validation
Before dispatching a generation job:
- The system checks: $\text{Account.DailyRemaining} \ge \text{Model.Cost} \times \text{Batch}$.
- If an account has 8 credits remaining and a Veo Lite job requires 10 credits, the account is skipped to prevent accidental deduction from the Pro monthly bank, seamlessly rotating to the next eligible account.

---

## 3. Creative Direction & Spatial Precision Upgrades

Addressing agent creative stiffness and asset layout collisions:

### 3.1 Structured Spatial Layout & Collision Avoidance
`PromoBeat` definitions are expanded with explicit spatial layout declarations:
* **Layout Zones:** `hero-center`, `split-left-text-right-broll`, `card-bottom-pill-top`, `pip-corner`.
* **Anchor & Bounding Boxes:** Every overlay element defines an anchor `{x, y, w, h, z_index}` constrained within platform safe margins (`safe_zone_for("9:16", 1080, 1920)`: top $\ge 220$px, bottom $\ge 380$px).
* **Zero-Collision Pre-Check:** Pre-render layout validation verifies that text headers, subtext, UI cards, and word-highlight captions never overlap in any frame.

### 3.2 Spring Physics Animation Engine (`html_motion.py` & `effectcraft.py`)
Porting Remotion's pure mathematical spring formula to native JS and Python:
$$\text{Scale}(t) = 1.0 - e^{-\zeta \omega_n t} \left(\cos(\omega_d t) + \frac{\zeta \omega_n}{\omega_d} \sin(\omega_d t)\right)$$
* Eliminates rigid linear and basic cubic easing.
* Provides organic bounce, snappy pop-in, and natural deceleration for metric cards, floating cursors, buttons, and badges.

### 3.3 Event-Driven Sound Design (Intra-Beat Audio Sync)
* **Continuous Audio-Visual Sync:** Replaces transition-only SFX cues with intra-beat event streams:
  - Progress counters emit rhythmic micro-pops (`ui-pop` at $-10$ dB) across the entire counting duration, terminating in an impact hit (`impact-hit` at $-3$ dB) precisely at 100% completion.
  - Interactive cards trigger click pops exactly when the animated cursor activates the target element ($t = 78\%$).

---

## 4. Empirical Technical Defect Resolutions (Task 10 Review)

1. **Elimination of `testsrc2` Checkerboard Corruption:**
   - Traced to dummy assets scaffolded with FFmpeg's diagnostic test pattern.
   - Replaced with `_deterministic_card` rendering an elegant dark radial brand gradient (`#0F172A` / `#38BDF8`).
2. **Elimination of YUV Plane Reinterpretation & Chroma Corruption:**
   - Traced to custom transition filters (`light_leak_warm`, `rgb_split_hit`) executing `geq` directly on `yuv420p` video streams and across the entire 30-second duration.
   - Resolved by wrapping effects in `format=rgb24` and strictly localizing execution to the transition window via `split/trim/concat`.
3. **ProRes 4444 Alpha Artifact Elimination:**
   - Replaced `prores_ks yuva444p10le` intermediate motion clips with lossless `png rgba` sequences, guaranteeing zero alpha premultiplication distortion.
4. **YouTube Official Bitrate & Color Profile Compliance:**
   - Enforced 1080p60 VBV targeting at **12 Mbps** (`-b:v 12M -maxrate 14M -bufsize 24M`) and 1080p30 at **8 Mbps**.
   - Enforced explicit BT.709 tags: `-color_range tv -colorspace bt709 -color_primaries bt709 -color_trc bt709`.

---

## 5. Implementation Roadmap (Phase 11)

| Task | Component | Scope & Deliverables |
|---|---|---|
| **11A** | **Google Flow Provider & Credit Manager** | Implement `src/media_studio/gen/providers/google_flow.py` with multi-account rotation (`flow-accounts.json`), `Nano Banana Pro` (0 credits), `Veo 3.1 - Lite` (10 credits), and `flow-credits.json` predictive ledger. |
| **11B** | **Spring Physics & Non-Colliding Layout Engine** | Embed `spring()` physics into `html_motion.py` and `effectcraft.py`; implement spatial layout zones and bounding-box collision prevention in `promo.py`. |
| **11C** | **Scene-Level Caching & Timeline Export** | Add content-addressed beat cache (`<project>/out/promo/cache/`) with `ms promo render --beat <idx>` selective regeneration; export canonical timelines (`timeline.otio.json` & `.mlt`). |
| **11D** | **Multimodal Visual Critic & Proving Ground Verification** | Implement `ms promo critique` returning structured actionable defects `{beat_index, defect, severity, corrective_action}`; re-render baseline with Veo 3.1 Lite & Nano Banana Pro. |
