import React from "react";
import "./PartCard.css";
import samplePhoto1 from "../assets/samplephoto1.jpg";
import samplePhoto2 from "../assets/samplephoto2.jpg";
import samplePhoto3 from "../assets/samplephoto3.jpg";
import samplePhoto4 from "../assets/samplephoto4.jpg";
import samplePhoto5 from "../assets/samplephoto5.jpg";

const samplePhotos = [samplePhoto1, samplePhoto2, samplePhoto3, samplePhoto4, samplePhoto5];

function PartCard({ part }) {
  if (!part) return null;

  const renderStars = (rating) => {
    if (!rating) return null;
    const stars = "★".repeat(Math.round(rating)) + "☆".repeat(5 - Math.round(rating));
    return <span className="part-rating-stars">{stars}</span>;
  };

  // Simple hash function to consistently pick an image based on PS number
  const getRandomImage = () => {
    const hash = part.ps_number?.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0) || 0;
    return samplePhotos[hash % samplePhotos.length];
  };

  return (
    <a href={part.part_url} target="_blank" rel="noopener noreferrer" className="part-card">
      <div className="part-card-image">
        <img src={getRandomImage()} alt={part.part_name} className="part-image" />
      </div>
      <div className="part-card-content">
        <div className="part-card-header">
          <h3 className="part-name">{part.part_name}</h3>
          <div className="part-price">${part.part_price?.toFixed(2)}</div>
        </div>
        <div className="part-card-meta">
          <span className="part-brand">{part.brand}</span>
          <div className="part-numbers">
            <span className="part-number-item">PS: {part.ps_number}</span>
            {part.manufacturer_part_number && (
              <span className="part-number-item">MFR: {part.manufacturer_part_number}</span>
            )}
          </div>
          {part.average_rating && (
            <span className="part-rating">
              {renderStars(part.average_rating)}
              <span className="rating-value">
                {part.average_rating.toFixed(1)} ({part.num_reviews} reviews)
              </span>
            </span>
          )}
        </div>
        <div className={`part-stock ${part.availability === "In Stock" ? "in-stock" : "out-of-stock"}`}>
          {part.availability}
        </div>
      </div>
    </a>
  );
}

export default PartCard;
