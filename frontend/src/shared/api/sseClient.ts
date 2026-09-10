/** POST SSE parser: preserves split UTF-8 characters and handles multi-line frames. */
export async function consumeSSE(
  response: Response,
  receive: (data: unknown) => void,
) {
  if (!response.ok || !response.body)
    throw new Error("连接失败，请检查后端服务");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  function drain(final = false) {
    let match: RegExpExecArray | null;
    while ((match = /\r?\n\r?\n/.exec(buffer))) {
      const frame = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);
      const data = frame
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (data) receive(JSON.parse(data));
    }
    if (final && buffer.trim() && !buffer.trim().startsWith(":"))
      throw new Error("响应意外中断，收到不完整事件");
  }
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      drain(done);
      if (done) break;
    }
  } finally {
    reader.releaseLock();
  }
}
