export const ACTION_TYPE = "ACTION_TYPE";

export const actionCreator = () => (dispatch) => {
  dispatch({
    type: ACTION_TYPE,
  });
};
