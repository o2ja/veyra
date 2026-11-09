import type { RouteObject } from "react-router-dom";

import { HomePage } from "../features/home/home-page";
import { AppLayout } from "./app-layout";
import { RouteError } from "./route-error";

export const routes: RouteObject[] = [
  {
    element: <AppLayout />,
    errorElement: <RouteError />,
    children: [{ index: true, element: <HomePage /> }],
  },
];
