import AlertCard from '../AlertCard';

export default function AlertCardExample() {
  return (
    <div className="w-96 space-y-3">
      <AlertCard type="fire" location="Kitchen" time="2:31 PM" status="active" />
      <AlertCard type="intrusion" location="Backyard" time="1:12 AM" status="resolved" />
      <AlertCard type="violence" location="Garage" time="11:45 PM" />
    </div>
  );
}
