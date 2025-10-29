import CameraFeed from '../CameraFeed';
import frontDoorImg from '@assets/generated_images/Front_door_camera_view_eee34996.png';

export default function CameraFeedExample() {
  return (
    <div className="w-80">
      <CameraFeed 
        name="Front Door"
        location="Entrance"
        status="active"
        imageUrl={frontDoorImg}
        timestamp="12:34:56 PM"
      />
    </div>
  );
}
