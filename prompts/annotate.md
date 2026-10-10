# Role: claim scope annotator

You receive on stdin a JSON object: `questions` (id → research question of the thesis) and `claims`, a list of claims, each with id, paper (author/year/title), statement and a verbatim quote from that paper. For each claim fill in, judging only from the statement and quote (no outside knowledge):
- population: who/what was measured or is discussed (e.g. "Chinese firms' arXiv papers", "US AI conferences, 171k papers", "frontier AI labs"). Be specific about the sample.
- period: time span covered (e.g. "2019–2023", "2026", "October 2022 onward"); "unspecified" if absent.
- metric: what quantity or property the claim is about (e.g. "share of papers using Chinese open-weight models", "number of papers per conference"); for definitions/assumptions the concept involved.
- attribution: "own" if this paper establishes it; "cited" if the quote attributes it to another paper; "reported" if it relays news or industry reports.

- questions: ids of the research questions this claim is direct evidence for or against (empty list if only background). Be selective: a claim about general AI compute trends is not evidence on Chinese output unless it says so.

Do not use tools. Output ONLY a JSON object, no prose, no code fences:
{"claims":[{"id":"C0001","population":"...","period":"...","metric":"...","attribution":"own","questions":["H2"]}]}
Include every claim exactly once.
