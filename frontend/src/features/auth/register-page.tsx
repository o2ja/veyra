import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link } from "react-router-dom";
import { z } from "zod";

import { Alert, AuthCard, Button, ErrorAlert, Field, fieldError } from "../../components/ui";
import { api } from "../../lib/api/client";
import { unwrap } from "../../lib/api/errors";
import { useApiSubmit } from "./use-api-form";

// Client checks are for convenience only; the server validates everything again.
const schema = z.object({
  full_name: z.string().trim().min(1, "Enter your name.").max(200),
  workspace_name: z.string().trim().min(1, "Enter your company name.").max(200),
  email: z.string().trim().email("Enter a valid email address."),
  password: z.string().min(12, "Use at least 12 characters.").max(256),
});
type Values = z.infer<typeof schema>;

export function RegisterPage() {
  const { register, handleSubmit, formState } = useForm<Values>({ resolver: zodResolver(schema) });
  const { busy, error, submit } = useApiSubmit();
  const [message, setMessage] = useState<string | null>(null);

  const onSubmit = handleSubmit(async (values) => {
    const result = await submit(() => unwrap(api.POST("/api/v1/auth/register", { body: values })));
    if (result) setMessage(result.message);
  });

  if (message) {
    return (
      <AuthCard title="Check your email">
        <Alert tone="info">{message}</Alert>
        <p className="mt-4 text-sm text-muted">The link expires in 24 hours. You can close this page.</p>
      </AuthCard>
    );
  }
  const e = formState.errors;
  return (
    <AuthCard title="Create your workspace">
      <form className="flex flex-col gap-4" onSubmit={onSubmit} noValidate>
        <ErrorAlert error={error?.details.length ? null : error} />
        <Field
          label="Your name"
          autoComplete="name"
          {...register("full_name")}
          error={e.full_name?.message ?? fieldError(error, "full_name")}
        />
        <Field
          label="Company name"
          autoComplete="organization"
          {...register("workspace_name")}
          error={e.workspace_name?.message ?? fieldError(error, "workspace_name")}
        />
        <Field
          label="Work email"
          type="email"
          autoComplete="email"
          {...register("email")}
          error={e.email?.message ?? fieldError(error, "email")}
        />
        <Field
          label="Password"
          type="password"
          autoComplete="new-password"
          hint="At least 12 characters. A long phrase works well."
          {...register("password")}
          error={e.password?.message ?? fieldError(error, "password")}
        />
        <Button type="submit" disabled={busy}>
          Create workspace
        </Button>
        <p className="text-sm text-muted">
          Already have an account?{" "}
          <Link className="text-brand underline" to="/login">
            Sign in
          </Link>
        </p>
      </form>
    </AuthCard>
  );
}
