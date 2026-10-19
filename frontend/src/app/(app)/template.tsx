"use client";

import { motion } from "framer-motion";
import type { ReactNode } from "react";

// Re-mounts on every navigation. Only a small vertical slide: content is never hidden.
export default function Template({ children }: { children: ReactNode }) {
  return (
    <motion.div
      initial={{ y: 6 }}
      animate={{ y: 0 }}
      transition={{ duration: 0.18, ease: [0.2, 0, 0, 1] }}
    >
      {children}
    </motion.div>
  );
}
