import AlertCard from '../AlertCard';

export default function AlertCardExample() {
  return (
    <div className="w-96 space-y-3">
      <AlertCard type="fire" location="Kitchen" time="2:31 PM" entity="Unknown" confidence={92} />
      <AlertCard type="intrusion" location="Backyard" time="1:12 AM" entity="Unknown" confidence={88} />
      <AlertCard type="violence" location="Garage" time="11:45 PM" entity="Jason P. (Neighbor)" confidence={85} />
    </div>
  );
}
