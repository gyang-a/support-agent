import { describe, it, expect } from "vitest";
import { consumeSSE } from "./sseClient";
function response(text: string, width = 1) {
  const data = new TextEncoder().encode(text);
  return new Response(
    new ReadableStream({
      start(controller) {
        for (let i = 0; i < data.length; i += width)
          controller.enqueue(data.slice(i, i + width));
        controller.close();
      },
    }),
  );
}
describe("SSE transport", () => {
  it("reassembles split Chinese UTF-8, CRLF and multiple events", async () => {
    const events: unknown[] = [];
    await consumeSSE(
      response(
        ': heartbeat\r\n\r\ndata: {"content":"你好"}\r\n\r\ndata: {"done":true}\n\n',
      ),
      (e) => events.push(e),
    );
    expect(events).toEqual([{ content: "你好" }, { done: true }]);
  });
  it("rejects truncated frames", async () => {
    await expect(
      consumeSSE(response('data: {"content":"未完成'), () => {}),
    ).rejects.toThrow("不完整");
  });
  it("rejects HTTP errors", async () => {
    await expect(
      consumeSSE(new Response("", { status: 503 }), () => {}),
    ).rejects.toThrow("连接失败");
  });
});
