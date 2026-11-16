import { afterEach, describe, expect, it, vi } from "vitest";
import { api, tokens } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
  tokens.clear();
});

describe("api.downloadBlob", () => {
  it("fetches an authenticated attachment and clicks a temporary download link", async () => {
    tokens.set("test-access-token", "test-refresh-token");
    const response = new Response(new Blob(["pdf-bytes"], { type: "application/pdf" }), {
      status: 200,
      headers: {
        "Content-Type": "application/pdf",
        "Content-Disposition": 'attachment; filename="Rev.A0.pdf"',
      },
    });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(response);
    let createdFilename = "";
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      createdFilename = this.download;
    });

    await api.downloadBlob("/transmittals/t1/attachments/a1/download", "Rev.A0.pdf");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/transmittals/t1/attachments/a1/download",
      expect.objectContaining({ credentials: "include" }),
    );
    expect(String(fetchMock.mock.calls[0]?.[0])).not.toContain("access_token");
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(request.headers).get("Authorization")).toBe("Bearer test-access-token");
    expect(click).toHaveBeenCalledTimes(1);
    expect(createdFilename).toBe("Rev.A0.pdf");
    expect(URL.createObjectURL).toHaveBeenCalled();
  });
});
