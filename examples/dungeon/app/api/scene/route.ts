import { getFlags } from "@/lib/flags";
import { roomSlug, scenePrompt } from "@/lib/scene";

// Generated room scene art (gated). Returns 204 (no art → cards keep the memory
// glimpse) unless the `sceneArt` flag is on AND the provider/Blob keys are set.
// Heavy deps are dynamically imported so they stay inert when the feature is off.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const NONE = () => new Response(null, { status: 204 });

export async function GET(req: Request) {
  const flags = await getFlags();
  if (!flags.sceneArt || !process.env.OPENAI_API_KEY || !process.env.BLOB_READ_WRITE_TOKEN) {
    return NONE();
  }
  const room = new URL(req.url).searchParams.get("room") ?? "a ruined chamber";
  const key = `dungeon/scene-${roomSlug(room)}.png`;
  try {
    const { list, put } = await import("@vercel/blob");
    const cached = await list({ prefix: key, limit: 1 });
    if (cached.blobs[0]) return Response.json({ url: cached.blobs[0].url });

    const { experimental_generateImage: generateImage } = await import("ai");
    const { openai } = await import("@ai-sdk/openai");
    const { image } = await generateImage({
      model: openai.image(process.env.SCENE_ART_MODEL ?? "dall-e-3"),
      prompt: scenePrompt(room),
      size: "1024x1024",
    });

    const blob = await put(key, Buffer.from(image.uint8Array), {
      access: "public",
      contentType: "image/png",
      addRandomSuffix: false,
    });
    return Response.json({ url: blob.url });
  } catch {
    return NONE();
  }
}
