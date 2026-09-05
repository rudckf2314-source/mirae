(function (global) {
  "use strict";

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function asText(value, fallback) {
    if (typeof value === "string" && value.trim()) return value;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    return fallback || "";
  }

  function normalizedStatus(value) {
    if (value === "success" || value === "clarify" || value === "safe_stop") return value;
    return "error";
  }

  function legacySource(item) {
    var source = item && typeof item === "object" ? item : {};
    return {
      title: [asText(source.filename), asText(source.location_type), asText(source.location)]
        .filter(Boolean)
        .join(" / ") || "검색 근거",
      detail: asText(source.text),
      score: asText(source.score),
      domain: "document",
      evidenceId: asText(source.chunk_id || source.document_id)
    };
  }

  function envelopeSource(item) {
    var source = item && typeof item === "object" ? item : {};
    return {
      title: [asText(source.domain), asText(source.source_file), asText(source.source_locator || source.source_page)]
        .filter(Boolean)
        .join(" / ") || "검증 근거",
      detail: asText(source.evidence_id || source.product_id || source.formula_version),
      score: "",
      domain: asText(source.domain),
      evidenceId: asText(source.evidence_id)
    };
  }

  function normalizeSearchResponse(data) {
    var payload = data && typeof data === "object" ? data : {};
    var isEnvelope = typeof payload.status === "string" &&
      (Object.prototype.hasOwnProperty.call(payload, "sources") ||
       Object.prototype.hasOwnProperty.call(payload, "next_action") ||
       Object.prototype.hasOwnProperty.call(payload, "question_id"));
    var sourceItems = isEnvelope ? asArray(payload.sources) : asArray(payload.results);

    return {
      responseType: isEnvelope ? "langgraph" : "legacy",
      status: isEnvelope ? normalizedStatus(payload.status) : "success",
      answer: asText(payload.answer, "답변을 생성하지 못했습니다."),
      sources: sourceItems.map(isEnvelope ? envelopeSource : legacySource),
      assumptions: isEnvelope ? asArray(payload.assumptions) : [],
      limitations: isEnvelope ? asArray(payload.limitations) : [],
      nextAction: isEnvelope ? asText(payload.next_action) : "",
      model: asText(payload.model),
      questionId: isEnvelope ? asText(payload.question_id) : ""
    };
  }

  global.normalizeSearchResponse = normalizeSearchResponse;
})(window);
