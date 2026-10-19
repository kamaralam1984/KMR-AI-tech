"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { TextField } from "@/components/ui/field";
import { PasswordField } from "@/components/ui/password-field";
import { apiRequest } from "@/lib/client-api";
import { safeNext } from "@/lib/org";
import type { User } from "@/lib/types";

export function LoginForm() {
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const [errors, setErrors] = useState<{ email?: string; password?: string; form?: string }>({});
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const email = String(data.get("email") ?? "").trim();
    const password = String(data.get("password") ?? "");
    const found: typeof errors = {};
    if (!email.includes("@")) found.email = "Enter your email address.";
    if (!password) found.password = "Enter your password.";
    setErrors(found);
    if (Object.keys(found).length > 0) return;

    setBusy(true);
    const result = await apiRequest<User>(
      "POST",
      "/auth/login",
      { email, password },
      { csrf: false },
    );
    if (result.ok) {
      router.replace(next);
      router.refresh();
      return;
    }
    setBusy(false);
    setErrors({ form: result.error.message });
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
      {errors.form ? (
        <Alert tone="danger" title="Could not sign in">
          {errors.form}
        </Alert>
      ) : null}
      <TextField
        label="Email"
        type="email"
        name="email"
        autoComplete="email"
        inputMode="email"
        autoCapitalize="none"
        required
        error={errors.email}
      />
      <PasswordField
        label="Password"
        name="password"
        autoComplete="current-password"
        required
        error={errors.password}
      />
      <Button type="submit" isLoading={busy}>
        Sign in
      </Button>
    </form>
  );
}
