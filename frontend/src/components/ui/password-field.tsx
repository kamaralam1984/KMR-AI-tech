"use client";

import { useState } from "react";

import { TextField, type TextFieldProps } from "./field";

/** Password input with a "Show password" checkbox, which helps a lot on phone keyboards. */
export function PasswordField(props: Omit<TextFieldProps, "type">) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="flex flex-col gap-2">
      <TextField {...props} type={visible ? "text" : "password"} />
      <label className="flex min-h-6 items-center gap-2 text-sm text-muted">
        <input
          type="checkbox"
          checked={visible}
          onChange={(event) => setVisible(event.target.checked)}
          className="size-5 accent-primary"
        />
        Show password
      </label>
    </div>
  );
}
