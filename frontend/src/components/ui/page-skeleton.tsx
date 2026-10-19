import { LoadingRegion, Skeleton } from "./skeleton";

export function PageSkeleton({ label = "Loading" }: { label?: string }) {
  return (
    <LoadingRegion label={label}>
      <div className="flex flex-col gap-4">
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    </LoadingRegion>
  );
}
