"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { TextField } from "@/components/ui/field";
import { PasswordField } from "@/components/ui/password-field";
import { apiRequest } from "@/lib/client-api";
import type { User } from "@/lib/types";

const MIN_PASSWORD = 12;

type Errors = { display_name?: string; email?: string; password?: string; form?: string };

export function RegisterForm() {
  const router = useRouter();
  const [errors, setErrors] = useState<Errors>({});
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const display_name = String(data.get("display_name") ?? "").trim();
    const email = String(data.get("email") ?? "").trim();
    const password = String(data.get("password") ?? "");
    const found: Errors = {};
    if (!display_name) found.display_name = "Enter your name.";
    if (!email.includes("@")) found.email = "Enter a valid email address.";
    if (password.length < MIN_PASSWORD) {
      found.password = `Use at least ${MIN_PASSWORD} characters.`;
    }
    setErrors(found);
    if (Object.keys(found).length > 0) return;

    setBusy(true);
    const result = await apiRequest<User>(
      "POST",
      "/auth/register",
      { display_name, email, password },
      { csrf: false },
    );
    if (result.ok) {
      router.replace("/dashboard");
      router.refresh();
      return;
    }
    setBusy(false);
    const { fieldErrors, message } = result.error;
    setErrors({
      display_name: fieldErrors.display_name,
      email: fieldErrors.email,
      password: fieldErrors.password,
      form: Object.keys(fieldErrors).length > 0 ? undefined : message,
    });
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
      {errors.form ? (
        <Alert tone="danger" title="Could not create the account">
          {errors.form}
        </Alert>
      ) : null}
      <TextField
        label="Your name"
        name="display_name"
        autoComplete="name"
        required
        maxLength={100}
        error={errors.display_name}
      />
      <TextField
        label="Work email"
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
        autoComplete="new-password"
        hint={`At least ${MIN_PASSWORD} characters. A short sentence works well.`}
        required
        error={errors.password}
      />
      <Button type="submit" isLoading={busy}>
        Create account
      </Button>
    </form>
  );
}
