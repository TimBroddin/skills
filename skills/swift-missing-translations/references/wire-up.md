# Swift-side wire-up patterns

Read this when the audit shows the catalog is 100% covered for a target
language but the UI still renders source-language strings on a device set
to that language. Each section is one cause + the fix.

## a. Custom view types holding `String` instead of `LocalizedStringKey`

```swift
// Symptom
struct MyRow { let label: String }     // call sites pass a Dutch literal,
                                       // Text(label) renders raw Dutch.

// Fix
struct MyRow { let label: LocalizedStringKey }
```

`LocalizedStringResource` is the alternative when you need to pass the
string into APIs outside SwiftUI (e.g. `Text(localizedResource)`).

## b. `String` enums for UI labels

```swift
// Symptom
enum AppSection {
    var title: String { ... }          // not localized
}

// Fix
enum AppSection {
    var title: LocalizedStringResource { ... }
}
```

Two gotchas:

- **`Hashable` breaks.** `LocalizedStringResource` is not `Hashable`. If the
  enclosing struct/enum needs `Hashable`, conform manually via an `id`,
  or drop the conformance if it was unused.
- **`.uppercased()` doesn't compile.** Switch the call site to a `Text`
  view + `.textCase(.uppercase)` modifier — display-only, doesn't break
  catalog resolution.

## c. Strings concatenated at runtime then passed to `Text(_:)`

```swift
// Symptom — builds a String first; Text sees an opaque string,
// no catalog key to look up.
let detail = "Tafel \(n), \(opLabel): \(avg)s gemiddeld"
return Text(verbatim: detail)
```

Two fixes, in order of preference:

1. **Inline the interpolation in `Text(_:)`.** `Text("Tafel \(n), …")`
   produces a `LocalizedStringKey` with `%lld`/`%@` placeholders that
   DOES go through the catalog.

2. **Format-string template + `String(format:locale:)`.** Use when the
   result has to be a plain `String` (e.g. it feeds an `AttributedString`
   for substring styling, or it's an accessibility value). The template
   is the catalog key:

   ```swift
   String(
       format: String(localized: "Tafel %lld, %@: %@s gemiddeld"),
       locale: locale, // for number/date formatting in interpolated values
       n, opLabel, avg
   )
   ```

   `%` in the catalog key must be escaped as `%%` in both the catalog
   value AND the Swift literal, otherwise `String(format:)` misreads it
   as a format specifier.

## d. `String(localized:)` ignores in-app language picker

**Only matters if the project has its own language picker** (e.g. a
`SettingsStore.selectedLanguage` injected as `\.locale` from the root
view). Skip this section otherwise.

`Text("…")` literals honour `\.locale` because SwiftUI threads it
through. But:

- `String(localized:)` reads `Bundle.main.preferredLocalizations` →
  device system language, ignores in-app picker.
- `String(localized:locale:)` ALSO ignores the picker for the catalog
  table lookup. The `locale:` argument only governs format-string
  interpolation, not table selection.

**Fix:** add a `LocaleBundle` helper that returns a `Bundle` rooted at
the matching `<lang>.lproj` directory inside `Bundle.main`:

```swift
import Foundation

enum LocaleBundle {
    static func bundle(for locale: Locale) -> Bundle {
        let code = locale.language.languageCode?.identifier ?? ""
        if let cached = cache[code] { return cached }
        if let path = Bundle.main.path(forResource: code, ofType: "lproj"),
           let b = Bundle(path: path) {
            cache[code] = b; return b
        }
        cache[code] = Bundle.main
        return Bundle.main
    }
    private static var cache: [String: Bundle] = [:]
}
```

Use it:

```swift
@Environment(\.locale) private var locale
// ...
let bundle = LocaleBundle.bundle(for: locale)
let text = String(localized: "key", bundle: bundle)
```

Apply to every `String(localized:)` call site that holds the result
before passing to a UI surface. Find them with
`grep -rn 'String(localized:'`.

## e. AppShortcuts.xcstrings (App Intents)

`AppShortcutsProvider` localizes Siri phrases via a sibling
`AppShortcuts.xcstrings`, not `Localizable.xcstrings`. Keys are the
literal phrases as written in Swift, with `${applicationName}` and
`${parameterName}` substitutions matching the Swift `\(.applicationName)`
/ `\(\.$param)` interpolations.

Verify with:

```bash
plutil -p <DerivedData>/.../<App>.app/en.lproj/AppShortcuts.strings
```

If the `.strings` is missing per locale, the catalog's substitution
shape probably doesn't match the Swift call site.

## f. Intent metadata + `AppEnum` parameters

`title`, `description`, `categoryName`, `parameterSummary`, parameter
`title`/`description` are typed `LocalizedStringResource` and DO go
through `Localizable.xcstrings` automatically.

But the AppIntents metadata processor rejects raw `Int` parameters
appearing inside an `AppShortcut` phrase. Wrap such parameters in an
`AppEnum`:

```swift
enum TableNumber: Int, AppEnum, CaseIterable {
    case one = 1, two, /* ... */ ten
    static let typeDisplayRepresentation: TypeDisplayRepresentation = "Tafel"
    static let caseDisplayRepresentations: [TableNumber: DisplayRepresentation] = [
        .one: "1", .two: "2", /* ... */
    ]
}
```

Then `Show the \(\.$table) times table` compiles and localizes.

## g. Legacy `{namedPlaceholder}` keys from a JS i18n catalog

If keys look like `Tafel {tableNumber} heeft meer oefening nodig` but
Swift call sites are `Text("Tafel \(n) heeft meer oefening nodig")`,
they never match. Common when the catalog was imported from lingui /
i18next / FormatJS.

Rename the catalog keys to use positional `%lld` / `%@`. Within each
language's translation value, substitute `{name}` → `%lld` (Int) or
`%@` (other) too — preserve the existing translations, don't lose them.
