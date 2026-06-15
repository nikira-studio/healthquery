import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "../App";

describe("App", () => {
  it("renders the HealthQuery shell", () => {
    render(<App />);
    expect(screen.getByText("HealthQuery")).toBeInTheDocument();
    expect(screen.getByText("Personal health dashboard")).toBeInTheDocument();
  });
});
