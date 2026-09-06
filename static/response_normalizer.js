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
    var domains = {product: "상품 자료", document: "안내 문서", law: "법령 자료", calculation: "계산 기준"};
    return {
      title: [asText(source.label || source.source_file, domains[source.domain] || "참고 자료"), source.source_page == null ? "" : asText(source.source_page) + "쪽"]
        .filter(Boolean)
        .join(" / ") || "검증 근거",
      detail: "",
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
      assumptions: isEnvelope ? asArray(payload.assumptions).map(function (item) {
        if (item && item.label) return item;
        return {label: "답변에 적용한 조건은 본문의 안내를 확인해 주세요."};
      }) : [],
      limitations: isEnvelope ? asArray(payload.limitations).map(function (item) {
        var text = asText(item);
        return /[A-Za-z]+_[A-Za-z_]+/.test(text) || !/[가-힣]/.test(text)
          ? "확인이 필요한 항목이 있어 답변에 제한이 있습니다." : text;
      }) : [],
      nextAction: isEnvelope ? asText(payload.next_action) : "",
      model: asText(payload.model),
      questionId: isEnvelope ? asText(payload.question_id) : ""
    };
  }

  global.normalizeSearchResponse = normalizeSearchResponse;
})(window);
