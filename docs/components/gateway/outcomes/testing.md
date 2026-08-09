# Gateway outcome algebra testing

Tests prove that the four discriminants are finite and exact, each variant is
frozen and typed, the JSON Schema accepts only the corresponding value/error
shape, and the algebra retains no native exception text, metadata, content,
credentials, path, or retry authority.

Effect-execution tests prove the semantic distinction: terminal absence or
rejection is `failed`, an earlier committed workflow effect is `partial`, and
an externally started effect without authoritative terminal evidence is
`outcome_unknown`.
