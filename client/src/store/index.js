import { combineReducers } from "redux";
import templateReducer from "./reducers/templateReducer";

const rootReducer = combineReducers({
  template: templateReducer,
});

export default rootReducer;
