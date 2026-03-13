export const maxDuration = 60;

export async function POST(request: Request) {
  const formData = await request.formData();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 60000);

  try {
    const res = await fetch("http://localhost:8000/api/upload", {
      method: "POST",
      body: formData,
      signal: controller.signal,
    });
    const data = await res.json();
    return Response.json(data, { status: res.status });
  } catch (err: unknown) {
    if (err instanceof Error && err.name === "AbortError") {
      return Response.json({ detail: "Upload timeout" }, { status: 504 });
    }
    return Response.json({ detail: "Upload failed" }, { status: 502 });
  } finally {
    clearTimeout(timeout);
  }
}
