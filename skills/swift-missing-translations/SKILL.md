---
name: swift-missing-translations
description: Audit a Swift/SwiftUI project's Localizable.xcstrings (and AppShortcuts.xcstrings) for missing translations, compute per-language coverage, find raw source-language literals still hard-coded in UI code, and bulk-translate the gaps. Source language is read from the catalog — works for any source language (en, nl, de, …).
---

# swift-missing-translations

End-to-end loop: **audit → translate → wire up Swift-side leaks → verify.** Use when the user wants to localize an iOS app or hunt down strings that haven't been translated yet.

## Step 0 — Reach shared understanding

Before running anything, derive what you can from the repo, then ask only what's left in a single batched message. Don't assume the source language.

**Read first:**
- `find . -name "*.xcstrings"` → catalog paths.
- For each catalog: `sourceLanguage` field, set of locales used (union of `localizations` keys).
- UI source roots — typical: `Features/`, `Views/`, `App/`, `Components/`, `Screens/`.
- In-app language picker? `grep -rn "selectedLanguage\|@AppStorage.*lang"` and look for `\.locale` injected at the root view. Determines whether `references/wire-up.md` §d applies.
- Sample 3 translated keys with placeholders to learn existing tone and the project's placeholder convention.

**Ask only when not derivable:**
- **Tone and audience** (kid-friendly second-person, professional B2B, etc.). Critical, not in the repo.
- Adding a brand-new locale? Only if the user named one not in the catalog.
- Dev/TestFlight quality vs App Store quality (the latter wants a native-speaker review pass after).

If the user invoked the skill bare, summarise what you found and what you intend to do, then wait for go-ahead.

## Step 1 — Audit

Run the bundled script:

```bash
python3 scripts/audit.py <project-root>
```

It outputs JSON with `coverage` (per-language counts) and `literals` (UI literals not in the catalog, with the `LocalizedStringKey` shape Swift would build and an `auto_localizes` flag).

Render coverage as one markdown table (don't reprint per phase). Group `literals` by file. Surface only the highlights to the user — full output is for your reasoning, not the chat log.

## Step 2 — Translate the gaps

For each missing-language cell, generate a translation. Hard rules:

- Preserve `%@`, `%lld`, `%.1f`, `%@%%` placeholders **in position, same count, same order**. Validate before writing — reject any translation where the multiset of placeholder tokens differs from the source.
- Identity-pass for symbol-only / format-only sources (`%@`, `=`, `→`, `·`, `×`, `✓`, brand names).
- Plurals: if the source has separate forms (`1 X` and `%lld Xs`), translate each separately.
- Tone: match what the user set at session start.

**Write back** to the xcstrings JSON: `state: "translated"`, preserve Xcode's pretty-print (2-space indent, alphabetical key order, trailing newline).

For >100-cell fills, checkpoint per language: write en, build, test compiled `.strings`, then de, etc. So a placeholder bug doesn't pollute every language at once.

Show 5 sample rows × all target languages **before** bulk-writing, so the user can sanity-check tone.

## Step 3 — Swift-side wire-up

The catalog can be 100% covered and the UI still ships in the source language. Read `references/wire-up.md` when the audit's `literals` list is non-empty *or* when the user reports a string still rendering in the source language despite a translated catalog.

Quick index of what's in there — load the file when one of these matches:

- `String`-typed view properties → §a
- `String` enum returning UI labels → §b (watch for `Hashable` + `.uppercased()` traps)
- runtime-built `String` passed to `Text(verbatim:)` / `AttributedString` → §c
- in-app language picker disagreeing with device locale → §d (`LocaleBundle` helper)
- `AppShortcuts.xcstrings` for Siri phrases → §e
- `AppEnum` for Int parameters in shortcut phrases → §f
- legacy `{name}` placeholders from a JS i18n import → §g

## Step 4 — Verify

1. Re-run `audit.py`. Coverage 100%, `literals` list empty (or only intentional cases — flag those).
2. `xcodebuild build` for the simulator. Catalog edits may not show until a clean build rewrites `<lang>.lproj/Localizable.strings`.
3. Spot-check compiled output for one or two new keys per locale:
   ```bash
   plutil -p <DerivedData>/.../<App>.app/de.lproj/Localizable.strings | grep "key fragment"
   ```
4. Install on a sim, **kill** the running app first (`xcrun simctl terminate <udid> <bundle-id>`), relaunch, walk the main flows. Running apps don't pick up new bundle resources.
5. Run the test suite. Localization shouldn't change behaviour; if a test fails it's likely a hard-coded source-language assertion that needs the same localization treatment.

## Output style

- One coverage table per request, not per phase.
- For Swift-side fixes, show a unified diff per file (apply via Edit), not the whole file.
- Commit only when the user says so. Default message: how many keys filled across how many languages, which Swift files were touched, build/test result.
- AI translations are dev/TestFlight quality. Tell the user upfront if they're targeting App Store submission — recommend a native-speaker review pass after.

## When to skip

- Project uses legacy `.strings` files (pre-iOS 17). Flag and offer to migrate; don't auto-edit.
- Repo has CI that auto-extracts catalog keys (Xcode build phase running `xcstringstool sync`). Work within that flow, don't manually edit auto-extracted keys.
