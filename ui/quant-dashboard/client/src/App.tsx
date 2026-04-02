import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/NotFound";
import { Route, Switch } from "wouter";
import ErrorBoundary from "./components/ErrorBoundary";
import { ThemeProvider } from "./contexts/ThemeContext";
import Dashboard from "./pages/Dashboard";
import ApiConfig from "./pages/ApiConfig";
import Positions from "./pages/Positions";
import StrategyPool from "./pages/StrategyPool";
import AlphaEngine from "./pages/AlphaEngine";
import TradingPairs from "./pages/TradingPairs";
import TradeHistory from "./pages/TradeHistory";
import DevProgress from "./pages/DevProgress";

function Router() {
  return (
    <Switch>
      <Route path="/" component={Dashboard} />
      <Route path="/api-config" component={ApiConfig} />
      <Route path="/positions" component={Positions} />
      <Route path="/strategies" component={StrategyPool} />
      <Route path="/alpha" component={AlphaEngine} />
      <Route path="/pairs" component={TradingPairs} />
      <Route path="/trades" component={TradeHistory} />
      <Route path="/dev-progress" component={DevProgress} />
      <Route path="/404" component={NotFound} />
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  return (
    <ErrorBoundary>
      <ThemeProvider defaultTheme="dark">
        <TooltipProvider>
          <Toaster theme="dark" />
          <Router />
        </TooltipProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}

export default App;
