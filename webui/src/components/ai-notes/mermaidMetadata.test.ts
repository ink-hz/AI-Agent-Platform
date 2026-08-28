import { describe, expect, it } from "vitest";

import { mermaidMetadata } from "./mermaidMetadata";


describe("mermaidMetadata", () => {
  it("extracts indented accessibility metadata", () => {
    expect(mermaidMetadata(`flowchart LR
      accTitle: RAG 查询链路
      accDescr: 从用户问题到引用校验和结果返回。`)).toEqual({
      title: "RAG 查询链路",
      description: "从用户问题到引用校验和结果返回。",
    });
  });

  it("trims values and falls back when metadata is absent or blank", () => {
    expect(mermaidMetadata("flowchart TB\n  accTitle:   \n  A-->B")).toEqual({
      title: "Mermaid 图表",
      description: null,
    });
    expect(mermaidMetadata("stateDiagram-v2\n  [*] --> Ready")).toEqual({
      title: "Mermaid 图表",
      description: null,
    });
  });
});
