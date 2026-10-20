# StudyProof AI implementation plan

The Study Coach uses a user-configured live OpenAI-compatible endpoint.
Local notes, the PDF library, study tracking and saved conversations remain local.

1. Maintain PDF import, extraction, viewing, source selection and persistence.
2. Maintain the GUI-free coach state machine, bounded source context, prompt construction, JSON schema and quotation validation.
3. Generate questions, hints, feedback and follow-ups through OpenAICompatProvider. Validate the API key and base URL before requests.
4. Require model-assigned confidence with feedback. Preserve the first answer and append revisions and follow-up links.
5. Retain one JSON repair retry, one retry for busy services, credential redaction, connection testing and cancellation. Failed turns show an error and store no generated turn.
6. Keep settings focused on base URL, supported model selection and masked API key. Normalize older settings to live mode.
7. Verify the provider boundary with fixed test replies, headless state-machine tests, native GUI integration and persistence round trips. Run PDF and data performance checks separately from external AI latency.

Release diagnostics use temporary study data without API credentials or network calls. They verify assets, PDF extraction/rendering, coach session creation, settings and save/reload. Full coaching behavior is covered by tests using injected fixed replies; real endpoint verification requires configured credentials.
