import { Card } from "../../components/ui";
import { useSession } from "../../lib/session";

export function HomePage() {
  const { me } = useSession();
  return (
    <Card title={me?.tenant?.name ?? "Workspace"}>
      <p className="text-sm text-muted">
        Signed in as {me?.user.email} ({me?.role}). Data import, agreements and claims arrive in later stages.
      </p>
    </Card>
  );
}
