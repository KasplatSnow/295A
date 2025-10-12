import { ACTION_TYPE } from "../actions/templateAction";

const initialState = {
  text: "React Application",
};

const templateReducer = (state = initialState, action) => {
  switch (action.type) {
    case ACTION_TYPE: {
      return state;
    }
    default:
      return state;
  }
};

export default templateReducer;
