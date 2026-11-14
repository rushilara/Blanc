# Datasets and Evaluation — Development Corpus, Not Automatic Criminal Labels

## 1. Why the datasets matter without fine-tuning

For the first release, published forensic collections are **source evidence**, **integration tests**, and **scenario benchmarks**. We embed extracted records with a pretrained embedder, retrieve real artifacts, and assess whether the answer/graph/timeline reflects the evidence. Most investigative scenarios do **not** provide a clean supervised table of suspicious versus benign messages. Training labels can be created later from verified case questions and relevant artifacts.

## 2. Recommended dataset registry

| Dataset | Why use it | Labels / ground truth | First role | Publisher link |
|---|---|---|---|---|
| **2019 Narcos (simplified, then complex)** | Simulated multi-person drug trafficking; disk and memory images; communications and other artifacts | Public scenario description; instructor guide encrypted; **not** per-message training labels | Flagship case if readable exports are available | https://digitalcorpora.org/corpora/scenarios/2019-narcos/ |
| **2019 Owl** | Simulated illegal wildlife-trade communications on computer and mobile | Narrative/scenario; no verified standardized row labels | Fallback/second case for graph/timeline | https://digitalcorpora.org/corpora/scenarios/2019-owl/ |
| **2008 M57-Jean** | Single-device corporate exfiltration scenario | Scenario/exercises, restricted solution materials | Smaller retrieval and timeline test | https://digitalcorpora.org/corpora/scenarios/m57-jean/ |
| **2009 M57-Patents** | Four-week company scenario, multiple daily disk/memory images and network packets | Scenario and exercises; instructor answers restricted | Later difficult multi-source test | https://digitalcorpora.org/corpora/scenarios/m57-patents-scenario/ |
| **Enron Email** | ~500k real workplace messages for communication search | No comprehensive criminal-activity labels; attachments absent in CMU distribution | Scale/search and conversation graphs; not a crime classifier | https://www.cs.cmu.edu/~enron/ |
| **NIST CFReDS Hacking/Data Leakage cases** | Simulated targeted forensic tasks | Question/answer and ground-truth detail varies; validate package first | Independent forensic evidence retrieval | https://cfreds.nist.gov/all |
| **NIST Cloud Test Data** | Known cloud artifacts with documentation/hashes | Artifact/documentation ground truth, not universal crime labels | Parser/source provenance testing | https://cfreds.nist.gov/all |
| **Aposemat IoT-23** | Labeled IoT malicious/benign traffic | Flow/behavior labels available | Later specialized cyberattack detector; not directly suitable for text retrieval fine-tuning | https://www.stratosphereips.org/datasets-iot23 |
| **Bot-IoT** | Large-scale attack/benign network traffic | Attack category/subcategory labels | Separate cyber detector research; check reuse terms | https://research.unsw.edu.au/projects/bot-iot-dataset |
| **CICIoT2023** | Multiple labeled attack categories | Benign and attack-family labels | Optional external cyber baseline | https://www.unb.ca/cic/datasets/iotdataset-2023.html |

NIST CFReDS is a catalog with contributed datasets, not a single standardized labeled training corpus. Publisher terms, download availability, record granularity and restricted answer keys should be checked before importing/redistributing anything.

## 3. First-90-minute dataset checkpoint

Before committing to Narcos as the actual demo:

- Recover one **readable primary record** (e.g., a chat, email or document) and a **materially independent second source** that answers the same investigative question.
- Capture source path, record identifier, excerpt and extraction notes for each.
- Include unrelated records from the same case in the index to make retrieval meaningful.
- Choose a question for which removing one record would weaken or change the support.
- Exclude case narrative spoilers, teacher guides and answer keys from the search index.
- If assets are inaccessible or extraction takes too long, switch to Owl or M57-Jean; never fabricate evidence to meet a demo script.

## 4. Small high-integrity evaluation set

Start with **3–5 manually verified questions per case**, not a large automatic question set. At least one question requires multiple artifacts; one is temporal; one is unanswerable or has conflicting evidence. For each question store:

- case ID, question ID, exact natural-language question;
- expected relevant source record IDs and source locators;
- expected evidence-backed claims, relationships, and events;
- which claim/edge/event changes when a pivotal record is excluded;
- uncertainty notes and how the answer key was validated by a human.

A second case using identical code is a **portability smoke test**, not proof of statistically reliable generalization.

## 5. Metrics and failure tests

| Layer | Quantitative / qualitative check |
|---|---|
| Retrieval | Did expected record IDs appear in top-k? Compare hybrid retrieval with keyword-only baseline. |
| Grounding | Proportion of factual claims, graph edges and events with valid source IDs and accurate excerpts. |
| Graph | Relation precision on manually verified edges; entity merge mistakes. |
| Timeline | Event/order consistency; correct timestamps, proper treatment of uncertain/undated dates. |
| Exclusion | Does the excluded record disappear from all retrievals? Does the right conclusion become weaker/unresolved? |
| Persistence | Saved case, graph, timeline, notes and exclusion states survive reload and follow-up questions. |
| Safety | Cross-case leakage, prompt injection from evidence text, unsupported inference, missing provenance. |

## 6. Later model research

After collecting verified cross-case examples, create query-positive-hard-negative triples for forensic embedder fine-tuning; compare general vs adapted embeddings on **held-out investigations**. Annotate entity mentions, relation edges and event spans for graph/timeline extractor improvements. Keep any training-derived case out of the held-out benchmark, and do not infer person-level criminality from association patterns.

**Verification note:** Scenario descriptions above are grounded in publicly available publisher pages. We have not downloaded or audited full underlying corpora or answer-key files for this v2 documentation package; first-hour dataset access is an explicit engineering gate.
