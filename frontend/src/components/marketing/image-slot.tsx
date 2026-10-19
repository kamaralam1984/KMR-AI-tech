import { existsSync } from "node:fs";
import path from "node:path";

import Image from "next/image";

import { Art } from "./art";

const EXTENSIONS = ["webp", "jpg", "jpeg", "png"] as const;

type Name = "scan" | "highlight" | "approve" | "verify";

/**
 * Shows a real photograph if one is present at public/images/<name>.(webp|jpg|png), otherwise the
 * drawn artwork. Drop your own licensed photos there; no stock image ships with the project.
 * The check runs at build/render time, so rebuild after adding a file.
 */
export function ImageSlot({ name, alt, sizes }: { name: Name; alt: string; sizes: string }) {
  const found = EXTENSIONS.find((ext) =>
    existsSync(path.join(process.cwd(), "public", "images", `${name}.${ext}`)),
  );
  if (found) {
    return (
      <Image
        src={`/images/${name}.${found}`}
        alt={alt}
        fill
        sizes={sizes}
        className="object-cover"
      />
    );
  }
  return <Art name={name} />;
}
