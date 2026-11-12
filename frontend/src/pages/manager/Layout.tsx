import { useEffect, type ReactNode } from "react";
import { useShell } from "../../ui/shell/Shell";

/**
 * The manager's chrome now lives in the route layout (ui/shell/Shell.tsx: ManagerLayout).
 * Screens still wrap themselves in this so `reviewCount` can keep the nav badge in step with a
 * queue the screen reloads itself.
 */
export default function ManagerPage({ children, reviewCount }: { children: ReactNode; reviewCount?: number }) {
  const { setReviewCount } = useShell();
  useEffect(() => {
    setReviewCount(reviewCount);
    return () => setReviewCount(undefined);
  }, [reviewCount, setReviewCount]);
  return <>{children}</>;
}
