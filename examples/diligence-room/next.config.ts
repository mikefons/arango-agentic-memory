import type { NextConfig } from "next";
import { withWorkflow } from "workflow/next";

const nextConfig: NextConfig = {};

// DR-6a: Vercel Workflows build plugin (transforms 'use workflow' / 'use step' directives).
export default withWorkflow(nextConfig);
